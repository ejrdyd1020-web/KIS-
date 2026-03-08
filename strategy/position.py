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
import logging
from datetime import datetime

# ※ check_stochastic_signal, get_ma120 은 순환 참조 방지를 위해
#   각 함수 내부에서 import 합니다.
from api.price  import get_current_price
from api.order  import sell_market
from config     import (
    STOP_LOSS_PCT, PRICE_CHECK_SEC, MARKET_CLOSE, DAILY_LOSS_LIMIT,
    BREAKOUT, REVERSION, STRATEGY_BREAKOUT, STRATEGY_REVERSION,
)

logger = logging.getLogger(__name__)

_positions: dict[str, dict] = {}

# ── 일일 손실 추적 ────────────────────────────────────────────
_daily_realized_loss: int = 0   # 당일 누적 실현손실 (원, 음수)
_daily_loss_halt    : bool = False  # 한도 초과 시 True → 전략 중단 신호

FORCE_SELL_TIME = "15:20"
NO_BUY_AFTER    = "15:20"


# ══════════════════════════════════════════
# 포지션 관리
# ══════════════════════════════════════════

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

    hard_stop   = int(avg_price * (1 + stop_loss_pct / 100))
    take_profit = int(avg_price * (1 + take_profit_pct / 100))

    _positions[code] = {
        "code"          : code,
        "name"          : name,
        "qty"           : qty,
        "avg_price"     : avg_price,
        "max_price"     : avg_price,       # 트레일링 스탑용 고점 추적
        "hard_stop"     : hard_stop,
        "take_profit"   : take_profit,
        "trail_min_pct" : trail_cfg["min_profit_pct"],
        "trail_drop_pct": trail_cfg["drop_pct"],
        "strategy_type" : strategy_type,
        "bought_at"     : datetime.now(),
    }
    logger.info(
        f"[{name}({code})] 포지션 등록 | 전략: {strategy_type} | "
        f"매입가: {avg_price:,}원 | "
        f"손절가: {hard_stop:,}원({stop_loss_pct:+.1f}%) | "
        f"익절가: {take_profit:,}원({take_profit_pct:+.1f}%)"
    )


def remove_position(code: str):
    if code in _positions:
        name = _positions[code]["name"]
        del _positions[code]
        logger.info(f"[{name}({code})] 포지션 제거")


def get_positions() -> dict:
    return _positions.copy()


def sync_positions_from_balance():
    from api.balance import get_balance
    data = get_balance()
    if not data:
        logger.warning("잔고 동기화 실패")
        return
    for s in data.get("stocks", []):
        if s["code"] not in _positions:
            add_position(s["code"], s["name"], s["qty"], s["avg_price"])
            logger.info(f"[{s['name']}] 기존 보유 종목 포지션 복원")


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

    pnl = (sell_price - avg_price) * qty   # 실현손익 (원)
    _daily_realized_loss += pnl

    pnl_pct = (sell_price - avg_price) / avg_price * 100

    logger.info(
        f"[{name}({code})] 실현손익 기록 | "
        f"손익: {pnl:+,}원 ({pnl_pct:+.2f}%) | "
        f"당일 누적 실현손익: {_daily_realized_loss:+,}원"
    )

    # 한도 체크 (손실만, 이익은 무시)
    if not DAILY_LOSS_LIMIT["enabled"]:
        return False

    if _daily_realized_loss <= -abs(DAILY_LOSS_LIMIT["max_loss_amt"]):
        _daily_loss_halt = True
        logger.critical(
            f"🚨 일일 최대 손실 한도 초과! "
            f"누적손실: {_daily_realized_loss:+,}원 / "
            f"한도: -{DAILY_LOSS_LIMIT['max_loss_amt']:,}원 "
            f"→ 자동매매 중단"
        )
        return True   # 중단 신호

    # 한도의 80% 도달 시 경고
    warn_amt = -abs(DAILY_LOSS_LIMIT["max_loss_amt"]) * 0.8
    if _daily_realized_loss <= warn_amt:
        logger.warning(
            f"⚠️ 일일 손실 한도 80% 도달! "
            f"누적손실: {_daily_realized_loss:+,}원 / "
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

def check_position(pos: dict) -> str:
    """
    단일 포지션 체크.

    Returns:
        "hard_stop"     : 1순위 — 고정 손절
        "ma120_stop"    : 2순위 — MA120 이탈
        "trailing_stop" : 3순위 — 트레일링 스탑
        "stoch_sell"    : 4순위 — 스토캐스틱 매도
        "market_close"  : 5순위 — 장마감 강제청산
        "hold"          : 유지
    """
    code = pos["code"]
    info = get_current_price(code)
    if not info:
        return "hold"

    cur_price  = info["price"]
    avg_price  = pos["avg_price"]
    now        = datetime.now().strftime("%H:%M")

    # 고점 갱신 (트레일링 스탑 기준)
    if cur_price > _positions.get(code, {}).get("max_price", 0):
        _positions[code]["max_price"] = cur_price

    profit_pct = (cur_price - avg_price) / avg_price * 100

    # ── 전략별 파라미터 (포지션 등록 시 저장된 값 사용) ──────
    trail_min_pct  = pos.get("trail_min_pct",  3.0)
    trail_drop_pct = pos.get("trail_drop_pct", 2.0)
    take_profit    = pos.get("take_profit",    0)     # 0이면 고정익절 미적용
    strategy_type  = pos.get("strategy_type",  STRATEGY_REVERSION)

    logger.debug(
        f"[{pos['name']}({strategy_type})] 현재가: {cur_price:,}원 | "
        f"수익률: {profit_pct:+.2f}% | "
        f"고점: {pos.get('max_price', cur_price):,}원 | "
        f"익절가: {take_profit:,}원"
    )

    # ── [1순위] 고정 손절 ──────────────────────────────────────
    if cur_price <= pos["hard_stop"]:
        logger.warning(
            f"[{pos['name']}] 🔴 고정손절! "
            f"{cur_price:,}원 ({profit_pct:+.2f}%) [{strategy_type}]"
        )
        return "hard_stop"

    # ── [1.5순위] 고정 익절 ────────────────────────────────────
    #   전략별 take_profit 가격 도달 시 즉시 익절
    if take_profit > 0 and cur_price >= take_profit:
        logger.info(
            f"[{pos['name']}] 💰 고정익절! "
            f"{cur_price:,}원 ({profit_pct:+.2f}%) [{strategy_type}]"
        )
        return "take_profit"

    # ── [2순위] MA120 이탈 ─────────────────────────────────────
    from strategy.condition import get_ma120
    ma120 = get_ma120(code)
    if ma120 and cur_price < ma120:
        logger.info(
            f"[{pos['name']}] 📉 MA120 이탈! "
            f"현재가: {cur_price:,} < MA120: {ma120:,.0f} [{strategy_type}]"
        )
        return "ma120_stop"

    # ── [3순위] 트레일링 스탑 (전략별 발동 기준 / 폭 적용) ────
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

    # ── [4순위] 스토캐스틱 매도 신호 ─────────────────────────────
    from strategy.condition import check_stochastic_signal
    if check_stochastic_signal(code) == "SELL":
        logger.info(f"[{pos['name']}] 🟣 스토캐스틱 과열 이탈 매도 [{strategy_type}]")
        return "stoch_sell"

    # ── [5순위] 장마감 강제청산 ───────────────────────────────────
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
    """매도 실행"""
    reason_map = {
        "hard_stop"    : "고정 손절",
        "take_profit"  : "고정 익절",
        "ma120_stop"   : "MA120 이탈 손절",
        "trailing_stop": "트레일링 익절",
        "stoch_sell"   : "스토캐스틱 매도",
        "market_close" : "장마감 강제청산",
    }
    label  = reason_map.get(reason, reason)
    result = sell_market(pos["code"], pos["qty"])

    if result["success"]:
        logger.info(f"[{pos['name']}] ✅ {label} 완료 | 주문번호: {result['order_no']}")

        # ── 실현손익 기록 + 일일 한도 체크 ──────────────────
        sell_price = result.get("price", pos["avg_price"])   # 체결가 (없으면 매입가로 대체)
        halted = record_realized_pnl(
            code       = pos["code"],
            name       = pos["name"],
            avg_price  = pos["avg_price"],
            sell_price = sell_price,
            qty        = pos["qty"],
        )
        if halted and stop_event:
            logger.critical("🚨 일일 손실 한도 초과 → stop_event 세팅, 자동매매 중단")
            stop_event.set()

        remove_position(pos["code"])
        return True
    else:
        logger.error(f"[{pos['name']}] ❌ {label} 실패 | {result['msg']}")
        return False


# ══════════════════════════════════════════
# 모니터링 루프
# ══════════════════════════════════════════

def run_monitor(stop_event=None):
    """포지션 모니터링 루프"""
    logger.info("📡 포지션 모니터링 시작")

    while True:
        if stop_event and stop_event.is_set():
            logger.info("포지션 모니터링 종료")
            break

        # ── 일일 손실 한도 초과 시 신규 매수만 차단, 모니터링은 유지 ──
        if is_daily_loss_halted():
            logger.debug("⛔ 일일 손실 한도 초과 상태 — 신규 매수 차단 중 (포지션 모니터링 유지)")

        positions = get_positions()
        if not positions:
            logger.debug("보유 포지션 없음 - 대기 중...")
        else:
            for code, pos in list(positions.items()):
                signal = check_position(pos)
                if signal != "hold":
                    execute_sell(pos, signal, stop_event=stop_event)

        time.sleep(PRICE_CHECK_SEC)


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
