# ============================================================
#  strategy/position.py
#
#  [매도 우선순위]
#    1. 고정 손절 (-3%)   → 안전망, 무조건 최우선
#    2. MA120 이탈        → 추세 붕괴 즉시 대응
#    3. 트레일링 스탑     → 수익 극대화 핵심
#    4. 스토캐스틱 매도   → 기술적 추세 반전 신호
#    5. 장마감 (15:20)    → 강제 청산
# ============================================================

import time
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# ※ check_stochastic_signal, get_ma120 은 순환 참조 방지를 위해
#   각 함수 내부에서 import 합니다.
from api.price  import get_current_price
from api.order  import sell_market
from config     import (
    STOP_LOSS_PCT, PRICE_CHECK_SEC, MARKET_CLOSE, DAILY_LOSS_LIMIT,
    BREAKOUT, REVERSION, STRATEGY_BREAKOUT, STRATEGY_REVERSION,
    TRADE_COST,
)
from utils.logger import get_logger

# ── 포지션 영속화 파일 경로 ────────────────────────────────────
_BASE_DIR       = Path(__file__).resolve().parent.parent
POSITIONS_FILE  = _BASE_DIR / "data" / "positions.json"

logger = get_logger("position")

_positions: dict[str, dict] = {}
_positions_lock = threading.Lock()   # breakout / reversion / monitor 동시 접근 보호
_loss_lock      = threading.Lock()   # _daily_realized_loss 동시 갱신 보호
_selling_codes: set[str] = set()     # 매도 진행 중 종목 (중복 실행 방지)
_selling_lock   = threading.Lock()   # _selling_codes 접근 보호

# ── 일일 손실 추적 ────────────────────────────────────────────
_daily_realized_loss: int = 0   # 당일 누적 실현손실 (원, 음수)
_daily_loss_halt    : bool = False  # 한도 초과 시 True → 전략 중단 신호

# ── MA120 체크 주기 관리 ──────────────────────────────────────
# 현재가 체크(5초)와 분리하여 API 과부하 방지
MA120_CHECK_INTERVAL = 60   # 60초마다 1회
_last_ma120_check: dict[str, float] = {}   # {code: timestamp}

FORCE_SELL_TIME = "15:20"
NO_BUY_AFTER    = "15:20"


# ══════════════════════════════════════════
# 포지션 관리
# ══════════════════════════════════════════

def _save_positions():
    """현재 포지션 전체를 파일에 저장 (strategy_type 포함)."""
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    serializable = {}
    for code, pos in _positions.items():
        entry = pos.copy()
        entry["bought_at"] = entry["bought_at"].isoformat() if isinstance(entry.get("bought_at"), datetime) else str(entry.get("bought_at", ""))
        serializable[code] = entry
    try:
        POSITIONS_FILE.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"포지션 파일 저장 실패: {e}")


def _load_positions():
    """재시작 시 파일에서 포지션 복원."""
    if not POSITIONS_FILE.exists():
        return
    try:
        data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        for code, entry in data.items():
            if "bought_at" in entry:
                try:
                    entry["bought_at"] = datetime.fromisoformat(entry["bought_at"])
                except Exception:
                    entry["bought_at"] = datetime.now()
            _positions[code] = entry
            logger.info(
                f"[{entry.get('name', code)}] 포지션 복원 | "
                f"전략: {entry.get('strategy_type', '?')} | "
                f"매입가: {entry.get('avg_price', 0):,}원 | "
                f"손절가: {entry.get('hard_stop', 0):,}원"
            )
        logger.info(f"포지션 파일 복원 완료: {len(data)}개")
    except Exception as e:
        logger.error(f"포지션 파일 복원 실패: {e}")


def add_position(code: str, name: str, qty: int, avg_price: int,
                 strategy_type: str = STRATEGY_REVERSION):
    """
    매수 완료 후 포지션 등록.
    strategy_type: STRATEGY_BREAKOUT 또는 STRATEGY_REVERSION
    """
    # 전략별 손절/익절 파라미터 선택
    cfg = BREAKOUT if strategy_type == STRATEGY_BREAKOUT else REVERSION

    stop_loss_pct  = cfg["stop_loss_pct"]
    take_profit_pct = cfg["take_profit_pct"]
    trail_cfg      = cfg["trailing_stop"]

    pre_stop_pct = cfg.get("pre_stop_pct", stop_loss_pct + 1.0)
    hard_stop    = int(avg_price * (1 + stop_loss_pct  / 100))
    pre_stop     = int(avg_price * (1 + pre_stop_pct   / 100))
    take_profit  = int(avg_price * (1 + take_profit_pct / 100))

    entry = {
        "code"          : code,
        "name"          : name,
        "qty"           : qty,
        "avg_price"     : avg_price,
        "max_price"     : avg_price,       # 트레일링 스탑용 고점 추적
        "hard_stop"     : hard_stop,
        "pre_stop"      : pre_stop,        # 사전 경고가 (집중 모니터링 전환)
        "pre_stop_mode" : False,           # True = 1초 간격 고속 모니터링 중
        "pre_order_no"  : "",              # 지정가 손절 예약 주문번호 (비면 미예약)
        "take_profit"   : take_profit,
        "trail_min_pct" : trail_cfg["min_profit_pct"],
        "trail_drop_pct": trail_cfg["drop_pct"],
        "strategy_type" : strategy_type,
        "bought_at"     : datetime.now(),
    }
    with _positions_lock:
        _positions[code] = entry
        _save_positions()
    logger.info(
        f"[{name}({code})] 포지션 등록 | 전략: {strategy_type} | "
        f"매입가: {avg_price:,}원 | "
        f"사전경고: {pre_stop:,}원({pre_stop_pct:+.1f}%) | "
        f"손절가: {hard_stop:,}원({stop_loss_pct:+.1f}%) | "
        f"익절가: {take_profit:,}원({take_profit_pct:+.1f}%)"
    )


def remove_position(code: str):
    with _positions_lock:
        if code in _positions:
            name = _positions[code]["name"]
            del _positions[code]
            _save_positions()
            logger.info(f"[{name}({code})] 포지션 제거")


def get_positions() -> dict:
    with _positions_lock:
        return _positions.copy()


def sync_positions_from_balance():
    """
    재시작 시 포지션 복원 순서:
      1. positions.json (strategy_type 포함) → 우선 복원
      2. KIS 잔고 API 교차 검증 → 파일에 없는 종목만 fallback 추가
    """
    # 1단계: 파일 복원 (strategy_type 보존)
    _load_positions()

    # 2단계: KIS 잔고 API로 교차 검증
    from api.balance import get_balance
    data = get_balance()
    if not data:
        logger.warning("잔고 API 조회 실패 — 파일 복원 결과만 사용")
        return
    for s in data.get("stocks", []):
        if s["code"] not in _positions:
            # 파일에 없는 종목 → strategy_type 알 수 없으니 REVERSION 기본값
            add_position(s["code"], s["name"], s["qty"], s["avg_price"])
            logger.warning(
                f"[{s['name']}] ⚠️ 포지션 파일 미존재 → KIS 잔고 기반 복원 "
                f"(strategy_type=REVERSION 기본값 적용)"
            )


# ══════════════════════════════════════════
# 일일 손실 한도 관리
# ══════════════════════════════════════════

def get_daily_loss_status() -> dict:
    """현재 일일 손실 현황 반환"""
    return {
        "realized_loss" : _daily_realized_loss,
        "max_loss_amt"  : DAILY_LOSS_LIMIT["max_loss_amt"],
        "halt"          : _daily_loss_halt,
        "remaining"     : DAILY_LOSS_LIMIT["max_loss_amt"] + _daily_realized_loss,
    }


def record_realized_pnl(code: str, name: str, avg_price: int, sell_price: int, qty: int) -> bool:
    """
    매도 완료 후 실현손익 기록 및 일일 손실 한도 체크.

    Args:
        code      : 종목코드
        name      : 종목명
        avg_price : 매입 평균가
        sell_price: 실제 매도가
        qty       : 수량

    Returns:
        True  → 한도 초과 (즉시 중단 필요)
        False → 정상 (계속 매매 가능)
    """
    global _daily_realized_loss, _daily_loss_halt

    # 수수료/세금 차감
    buy_fee   = int(avg_price  * qty * TRADE_COST["buy_fee"])
    sell_fee  = int(sell_price * qty * TRADE_COST["sell_fee"])
    sell_tax  = int(sell_price * qty * TRADE_COST["sell_tax"])
    total_fee = buy_fee + sell_fee + sell_tax

    gross_pnl = (sell_price - avg_price) * qty          # 수수료 전
    net_pnl   = gross_pnl - total_fee                   # 수수료/세금 차감 후
    pnl_pct   = (sell_price - avg_price) / avg_price * 100

    with _loss_lock:
        _daily_realized_loss += net_pnl
        current_loss = _daily_realized_loss

    logger.info(
        f"[{name}({code})] 실현손익 기록 | "
        f"총손익: {gross_pnl:+,}원 → 비용({total_fee:,}원) 차감 → "
        f"순손익: {net_pnl:+,}원 ({pnl_pct:+.2f}%) | "
        f"당일 누적: {current_loss:+,}원"
    )

    # 한도 체크 (손실만, 이익은 무시)
    if not DAILY_LOSS_LIMIT["enabled"]:
        return False

    if current_loss <= -abs(DAILY_LOSS_LIMIT["max_loss_amt"]):
        _daily_loss_halt = True
        logger.critical(
            f"🚨 일일 최대 손실 한도 초과! "
            f"누적손실: {current_loss:+,}원 / "
            f"한도: -{DAILY_LOSS_LIMIT['max_loss_amt']:,}원 "
            f"→ 신규 매수 중단 + 기존 포지션 손절가 타이트 조정(-0.5%)"
        )
        # 기존 포지션 손절가를 현재가 -0.5%로 타이트하게 조정
        # (강제청산 대신 추가 손실만 최소화)
        with _positions_lock:
            for pos_code, pos in _positions.items():
                from api.price import get_current_price
                info = get_current_price(pos_code)
                if info and info["price"]:
                    tight_stop = int(info["price"] * 0.995)   # 현재가 -0.5%
                    if tight_stop > pos["hard_stop"]:          # 기존 손절보다 높을 때만 상향
                        old_stop = pos["hard_stop"]
                        pos["hard_stop"] = tight_stop
                        logger.warning(
                            f"[{pos['name']}] 손절가 상향 조정: "
                            f"{old_stop:,}원 → {tight_stop:,}원 (한도초과 타이트)"
                        )
        return True   # 중단 신호

    # 한도의 80% 도달 시 경고
    warn_amt = -abs(DAILY_LOSS_LIMIT["max_loss_amt"]) * 0.8
    if current_loss <= warn_amt:
        logger.warning(
            f"⚠️ 일일 손실 한도 80% 도달! "
            f"누적손실: {current_loss:+,}원 / "
            f"한도: -{DAILY_LOSS_LIMIT['max_loss_amt']:,}원"
        )

    return False


def is_daily_loss_halted() -> bool:
    """일일 손실 한도 초과 여부 확인 (strategy 루프에서 호출)"""
    return _daily_loss_halt


def reset_daily_loss():
    """하루 시작 시 초기화 (필요 시 main.py에서 호출)"""
    global _daily_realized_loss, _daily_loss_halt
    _daily_realized_loss = 0
    _daily_loss_halt     = False
    logger.info("일일 손실 추적 초기화 완료")


def is_buyable_time() -> bool:
    """15:20 이후 신규 매수 차단"""
    now = datetime.now().strftime("%H:%M")
    if now >= NO_BUY_AFTER:
        logger.debug(f"[매수 차단] {now} → {NO_BUY_AFTER} 이후 신규 매수 금지")
        return False
    return True


# ══════════════════════════════════════════
# 포지션 체크 (매도 우선순위)
# ══════════════════════════════════════════

def _place_pre_order(pos: dict):
    """
    -2% 도달 시 hard_stop 가격으로 지정가 매도 예약.
    성공 시 포지션에 pre_order_no 저장.
    """
    from api.order import sell_limit
    code  = pos["code"]
    price = pos["hard_stop"]
    result = sell_limit(code, pos["qty"], price)
    if result["success"]:
        with _positions_lock:
            if code in _positions:
                _positions[code]["pre_order_no"] = result["order_no"]
                _positions[code]["pre_stop_mode"] = True
        logger.warning(
            f"[{pos['name']}] 🟠 지정가 손절 예약 | "
            f"{price:,}원 × {pos['qty']}주 | 주문번호: {result['order_no']}"
        )
    else:
        logger.error(f"[{pos['name']}] 지정가 예약 실패 → 1초 모니터링으로 대체: {result['msg']}")
        with _positions_lock:
            if code in _positions:
                _positions[code]["pre_stop_mode"] = True   # 예약 실패해도 집중 모니터링은 유지


def _cancel_pre_order(pos: dict):
    """
    가격 회복 시 예약된 지정가 매도 취소.
    취소 성공: pre_order_no / pre_stop_mode 초기화.
    취소 실패: 이미 체결됐을 가능성 → 호출부에서 포지션 정리.
    """
    from api.order import cancel_order
    code     = pos["code"]
    order_no = pos.get("pre_order_no", "")
    if not order_no:
        return True   # 예약 없음, 정상

    result = cancel_order(order_no, code, pos["qty"])
    if result["success"]:
        with _positions_lock:
            if code in _positions:
                _positions[code]["pre_order_no"] = ""
                _positions[code]["pre_stop_mode"] = False
        logger.info(
            f"[{pos['name']}] ✅ 지정가 예약 취소 완료 (가격 회복) | 주문번호: {order_no}"
        )
        return True
    else:
        logger.warning(
            f"[{pos['name']}] ⚠️ 지정가 예약 취소 실패 | {result['msg']} "
            f"→ 이미 체결됐을 가능성 있음"
        )
        return False   # 취소 실패 → 호출부에서 이미 체결로 간주해 포지션 정리


def check_position(pos: dict) -> str:
    """
    단일 포지션 체크.

    API 호출 최적화:
      - 현재가: 매 호출마다 (5초 주기, 손절·익절 즉시 반응)
      - MA120 : 60초마다 1회 (추세 확인, API 과부하 방지)
      - 스토캐스틱 매도: 제거 (이미 고정손절·익절·트레일링으로 커버됨)

    Returns:
        "hard_stop"     : 1순위 — 고정 손절
        "take_profit"   : 2순위 — 고정 익절
        "ma120_stop"    : 3순위 — MA120 이탈 (60초 주기)
        "trailing_stop" : 4순위 — 트레일링 스탑
        "market_close"  : 5순위 — 장마감 강제청산
        "hold"          : 유지
    """
    code = pos["code"]
    info = get_current_price(code)
    if not info:
        return "hold"

    cur_price     = info["price"]
    avg_price     = pos["avg_price"]
    now           = datetime.now().strftime("%H:%M")
    now_ts        = time.time()

    # 고점 갱신 (트레일링 스탑 기준)
    with _positions_lock:
        if code in _positions and cur_price > _positions[code].get("max_price", 0):
            _positions[code]["max_price"] = cur_price

    profit_pct     = (cur_price - avg_price) / avg_price * 100
    trail_min_pct  = pos.get("trail_min_pct",  3.0)
    trail_drop_pct = pos.get("trail_drop_pct", 2.0)
    take_profit    = pos.get("take_profit",    0)
    strategy_type  = pos.get("strategy_type",  STRATEGY_REVERSION)

    logger.debug(
        f"[{pos['name']}({strategy_type})] 현재가: {cur_price:,}원 | "
        f"수익률: {profit_pct:+.2f}% | "
        f"고점: {pos.get('max_price', cur_price):,}원"
    )

    # ── [1순위] 고정 손절 ──────────────────────────────────────
    if cur_price <= pos["hard_stop"]:
        logger.warning(
            f"[{pos['name']}] 🔴 고정손절! "
            f"{cur_price:,}원 ({profit_pct:+.2f}%) [{strategy_type}]"
        )
        return "hard_stop"

    # ── [1.5순위] 사전 경고 (pre_stop) ────────────────────────
    # -2% 진입 → 지정가 손절 예약 + 1초 집중 모니터링
    if pos.get("pre_stop", 0) and cur_price <= pos["pre_stop"]:
        if not pos.get("pre_order_no"):
            # 아직 예약 없음 → 지정가 주문 즉시 제출
            _place_pre_order(pos)
            logger.warning(
                f"[{pos['name']}] 🟠 사전경고 진입! "
                f"{cur_price:,}원 ({profit_pct:+.2f}%) [{strategy_type}]"
            )
        return "pre_stop"

    # ── 가격 회복: pre_stop 위로 올라온 경우 → 예약 취소 ────
    if pos.get("pre_order_no") and cur_price > pos.get("pre_stop", 0):
        cancelled = _cancel_pre_order(pos)
        if not cancelled:
            # 취소 실패 = 지정가 주문이 이미 체결됨
            # → hard_stop 가격으로 체결된 것으로 처리
            logger.warning(
                f"[{pos['name']}] 지정가 주문 이미 체결됨 (가격 회복 중 취소 실패) "
                f"→ {pos['hard_stop']:,}원 체결로 포지션 정리"
            )
            return "pre_order_filled"
        logger.info(
            f"[{pos['name']}] 💹 가격 회복! {cur_price:,}원 ({profit_pct:+.2f}%) "
            f"→ 지정가 예약 취소, 정상 모니터링 복귀"
        )

    # ── [2순위] 고정 익절 ──────────────────────────────────────
    if take_profit > 0 and cur_price >= take_profit:
        logger.info(
            f"[{pos['name']}] 💰 고정익절! "
            f"{cur_price:,}원 ({profit_pct:+.2f}%) [{strategy_type}]"
        )
        return "take_profit"

    # ── [3순위] MA120 이탈 (60초 주기) ────────────────────────
    last_check = _last_ma120_check.get(code, 0)
    if now_ts - last_check >= MA120_CHECK_INTERVAL:
        _last_ma120_check[code] = now_ts
        from strategy.strategy_reversion import get_ma120
        ma120 = get_ma120(code)
        if ma120 and cur_price < ma120:
            logger.info(
                f"[{pos['name']}] 📉 MA120 이탈! "
                f"현재가: {cur_price:,} < MA120: {ma120:,.0f} [{strategy_type}]"
            )
            return "ma120_stop"

    # ── [4순위] 트레일링 스탑 ──────────────────────────────────
    if profit_pct >= trail_min_pct:
        max_price = pos.get("max_price", cur_price)
        drop_pct  = (max_price - cur_price) / max_price * 100
        if drop_pct >= trail_drop_pct:
            logger.info(
                f"[{pos['name']}] 🟢 트레일링 스탑! "
                f"고점: {max_price:,} → 현재: {cur_price:,} "
                f"({drop_pct:.1f}% 하락) [{strategy_type}]"
            )
            return "trailing_stop"

    # ── [5순위] 장마감 강제청산 ───────────────────────────────
    if now >= FORCE_SELL_TIME:
        logger.info(
            f"[{pos['name']}] ⏰ 장마감 강제청산 "
            f"({cur_price:,}원, {profit_pct:+.2f}%)"
        )
        return "market_close"

    return "hold"


# ══════════════════════════════════════════
# 매도 실행
# ══════════════════════════════════════════

def execute_sell(pos: dict, reason: str, stop_event=None) -> bool:
    """매도 실행 (최대 3회 재시도)"""
    reason_map = {
        "hard_stop"       : "고정 손절",
        "pre_order_filled": "지정가 손절 체결",
        "take_profit"     : "고정 익절",
        "ma120_stop"      : "MA120 이탈 손절",
        "trailing_stop"   : "트레일링 익절",
        "stoch_sell"      : "스토캐스틱 매도",
        "market_close"    : "장마감 강제청산",
    }
    label = reason_map.get(reason, reason)

    # ── 지정가 주문이 이미 체결된 경우 → 시장가 없이 PnL만 기록 ──
    if reason == "pre_order_filled":
        logger.info(f"[{pos['name']}] ✅ {label} | 체결가: {pos['hard_stop']:,}원")
        _finalize_sell(pos, sell_price=pos["hard_stop"], label=label, stop_event=stop_event)
        return True

    # ── hard_stop: 기존 지정가 예약이 있으면 먼저 취소 시도 ──────
    if reason == "hard_stop" and pos.get("pre_order_no"):
        cancelled = _cancel_pre_order(pos)
        if not cancelled:
            # 취소 실패 = 지정가로 이미 체결 → 시장가 중복 매도 방지
            logger.info(
                f"[{pos['name']}] hard_stop 도달 but 지정가 이미 체결 → 시장가 생략, 포지션 정리"
            )
            _finalize_sell(pos, sell_price=pos["hard_stop"], label="지정가 손절 체결", stop_event=stop_event)
            return True

    result = {"success": False}
    for attempt in range(1, 4):
        result = sell_market(pos["code"], pos["qty"])
        if result["success"]:
            break
        logger.warning(
            f"[{pos['name']}] ⚠️ {label} 매도 실패 "
            f"({attempt}/3) | {result.get('msg', '')}"
        )
        if attempt < 3:
            time.sleep(1)
    else:
        logger.critical(
            f"[{pos['name']}({pos['code']})] 🚨 매도 3회 연속 실패 — "
            f"수동 개입 필요! 사유: {label}"
        )
        return False

    if result["success"]:
        logger.info(f"[{pos['name']}] ✅ {label} 완료 | 주문번호: {result['order_no']}")
        # 시장가 체결가: 현재가 조회 (실패 시 avg_price fallback)
        _price_info = get_current_price(pos["code"])
        sell_price  = _price_info.get("price", 0) if _price_info else 0
        if sell_price <= 0:
            sell_price = pos["avg_price"]
            logger.warning(
                f"[{pos['name']}] 체결가 조회 실패 → 매입가({sell_price:,}원)로 PnL 계산 (오차 있음)"
            )
        _finalize_sell(pos, sell_price=sell_price, label=label, stop_event=stop_event)
        return True
    else:
        logger.error(f"[{pos['name']}] ❌ {label} 실패 | {result['msg']}")
        return False


def _finalize_sell(pos: dict, sell_price: int, label: str, stop_event=None):
    """PnL 기록 + 포지션 제거 (시장가/지정가 공통)"""
    code = pos["code"]

    # ── 중복 호출 방지: 포지션 존재 확인 + 제거를 원자적으로 처리 ──
    # executor 큐에 쌓인 동일 종목 태스크가 재진입하는 것을 차단한다.
    with _positions_lock:
        if code not in _positions:
            logger.debug(f"[{pos['name']}({code})] _finalize_sell 중복 호출 무시")
            return
        del _positions[code]
        _save_positions()

    halted = record_realized_pnl(
        code       = code,
        name       = pos["name"],
        avg_price  = pos["avg_price"],
        sell_price = sell_price,
        qty        = pos["qty"],
    )
    if halted and stop_event:
        logger.critical("🚨 일일 손실 한도 초과 → stop_event 세팅, 자동매매 중단")
        stop_event.set()
    try:
        from strategy.condition import remove_from_bought_codes
        remove_from_bought_codes(code)
    except Exception:
        pass


# ══════════════════════════════════════════
# 모니터링 루프
# ══════════════════════════════════════════

def _check_and_sell(code: str, pos: dict, stop_event=None):
    """단일 포지션 체크 + 매도 — 독립 쓰레드에서 실행"""
    # 이미 매도 진행 중인 종목은 건너뜀
    with _selling_lock:
        if code in _selling_codes:
            return
    signal = check_position(pos)
    if signal in ("hold", "pre_stop"):
        return
    # 매도 실행 (중복 방지 플래그 ON)
    with _selling_lock:
        if code in _selling_codes:
            return
        _selling_codes.add(code)
    try:
        execute_sell(pos, signal, stop_event=stop_event)
    finally:
        with _selling_lock:
            _selling_codes.discard(code)


def run_monitor(stop_event=None):
    """포지션 모니터링 루프 — 병렬 처리 + pre_stop 집중 모니터링"""
    logger.info("📡 포지션 모니터링 시작 (병렬)")

    executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="pos_monitor")

    while True:
        if stop_event and stop_event.is_set():
            logger.info("포지션 모니터링 종료")
            executor.shutdown(wait=False)
            break

        if is_daily_loss_halted():
            logger.debug("⛔ 일일 손실 한도 초과 상태 — 신규 매수 차단 중 (포지션 모니터링 유지)")

        positions = get_positions()
        if not positions:
            logger.debug("보유 포지션 없음 - 대기 중...")
            time.sleep(PRICE_CHECK_SEC)
            continue

        # ── 모든 포지션 병렬 체크 ────────────────────────────
        for code, pos in positions.items():
            executor.submit(_check_and_sell, code, pos, stop_event)

        # ── PRICE_CHECK_SEC 동안 pre_stop 종목만 1초마다 재체크 ──
        for _ in range(PRICE_CHECK_SEC):
            time.sleep(1)
            if stop_event and stop_event.is_set():
                break
            fast_positions = {
                k: v for k, v in get_positions().items()
                if v.get("pre_stop_mode")
            }
            for code, pos in fast_positions.items():
                executor.submit(_check_and_sell, code, pos, stop_event)


def print_positions():
    """포지션 현황 출력"""
    positions = get_positions()
    print(f"\n{'='*65}")
    print(f"  📋 현재 보유 포지션 ({len(positions)}개)")
    print(f"{'='*65}")

    if not positions:
        print("  보유 포지션 없음")
    else:
        for pos in positions.values():
            info = get_current_price(pos["code"])
            cur  = info.get("price", 0) if info else 0
            pct  = (cur - pos["avg_price"]) / pos["avg_price"] * 100 if cur else 0
            sign = "+" if pct >= 0 else ""

            print(f"  {pos['name']} ({pos['code']})")
            print(f"    수량: {pos['qty']:,}주 | 매입가: {pos['avg_price']:,}원 | 현재가: {cur:,}원")
            print(f"    고점: {pos.get('max_price', 0):,}원 | 손절가: {pos['hard_stop']:,}원")
            print(f"    수익률: {sign}{pct:.2f}%")
    print(f"{'='*65}\n")
