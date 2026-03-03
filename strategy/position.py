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

from strategy.condition import check_stochastic_signal, get_ma120
from api.price  import get_current_price
from api.order  import sell_market
from config     import STOP_LOSS_PCT, PRICE_CHECK_SEC, MARKET_CLOSE

logger = logging.getLogger(__name__)

_positions: dict[str, dict] = {}

FORCE_SELL_TIME = "15:20"
NO_BUY_AFTER    = "15:20"


# ══════════════════════════════════════════
# 포지션 관리
# ══════════════════════════════════════════

def add_position(code: str, name: str, qty: int, avg_price: int):
    """매수 완료 후 포지션 등록"""
    hard_stop = int(avg_price * (1 - abs(STOP_LOSS_PCT) / 100))

    _positions[code] = {
        "code"      : code,
        "name"      : name,
        "qty"       : qty,
        "avg_price" : avg_price,
        "max_price" : avg_price,   # 트레일링 스탑용 고점 추적
        "hard_stop" : hard_stop,
        "bought_at" : datetime.now(),
    }
    logger.info(
        f"[{name}({code})] 포지션 등록 | "
        f"매입가: {avg_price:,}원 | 손절가: {hard_stop:,}원({STOP_LOSS_PCT:+.1f}%)"
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

    logger.debug(
        f"[{pos['name']}] 현재가: {cur_price:,}원 | "
        f"수익률: {profit_pct:+.2f}% | "
        f"고점: {pos.get('max_price', cur_price):,}원"
    )

    # ── [1순위] 고정 손절 ──────────────────────────────────────
    if cur_price <= pos["hard_stop"]:
        logger.warning(
            f"[{pos['name']}] 🔴 고정손절! "
            f"{cur_price:,}원 ({profit_pct:+.2f}%)"
        )
        return "hard_stop"

    # ── [2순위] MA120 이탈 ─────────────────────────────────────
    ma120 = get_ma120(code)
    if ma120 and cur_price < ma120:
        logger.info(
            f"[{pos['name']}] 📉 MA120 이탈! "
            f"현재가: {cur_price:,} < MA120: {ma120:,.0f}"
        )
        return "ma120_stop"

    # ── [3순위] 트레일링 스탑 (수익 3% 이상 시 작동) ──────────────
    if profit_pct >= 3.0:
        max_price = pos.get("max_price", cur_price)
        drop_pct  = (max_price - cur_price) / max_price * 100
        if drop_pct >= 2.0:
            logger.info(
                f"[{pos['name']}] 🟢 트레일링 스탑! "
                f"고점: {max_price:,} → 현재: {cur_price:,} "
                f"({drop_pct:.1f}% 하락)"
            )
            return "trailing_stop"

    # ── [4순위] 스토캐스틱 매도 신호 ─────────────────────────────
    if check_stochastic_signal(code) == "SELL":
        logger.info(f"[{pos['name']}] 🟣 스토캐스틱 과열 이탈 매도")
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

def execute_sell(pos: dict, reason: str) -> bool:
    """매도 실행"""
    reason_map = {
        "hard_stop"    : "고정 손절",
        "ma120_stop"   : "MA120 이탈 손절",
        "trailing_stop": "트레일링 익절",
        "stoch_sell"   : "스토캐스틱 매도",
        "market_close" : "장마감 강제청산",
    }
    label  = reason_map.get(reason, reason)
    result = sell_market(pos["code"], pos["qty"])

    if result["success"]:
        logger.info(f"[{pos['name']}] ✅ {label} 완료 | 주문번호: {result['order_no']}")
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

        positions = get_positions()
        if not positions:
            logger.debug("보유 포지션 없음 - 대기 중...")
        else:
            for code, pos in list(positions.items()):
                signal = check_position(pos)
                if signal != "hold":
                    execute_sell(pos, signal)

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
