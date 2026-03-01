# ============================================================
#  monitor/position.py  –  손절(MA20)/익절/장마감/체결강도 모니터링
# ============================================================

import time
import sys
import os
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from api.price    import get_current_price
from api.chart    import get_ma40_1min
from api.order    import sell_market
from utils.logger import get_logger
from config import (
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    PRICE_CHECK_SEC,
    MARKET_CLOSE,
    MA20_STOP_LOSS,
)

logger = get_logger("monitor")

_positions: dict[str, dict] = {}

EXEC_STRENGTH_TIMEOUT = 60   # 체결강도 약세 허용 시간 (초)


def add_position(code: str, name: str, qty: int, avg_price: int):
    """매수 완료 후 포지션 등록"""
    # 고정 손절가 (MA20 손절 실패 시 안전망)
    hard_stop = int(avg_price * (1 + STOP_LOSS_PCT / 100))
    take_profit = int(avg_price * (1 + TAKE_PROFIT_PCT / 100))

    _positions[code] = {
        "code"        : code,
        "name"        : name,
        "qty"         : qty,
        "avg_price"   : avg_price,
        "hard_stop"   : hard_stop,     # 고정 손절가 (안전망)
        "take_profit" : take_profit,
        "bought_at"   : datetime.now(),
        "weak_since"  : None,          # 체결강도 약세 시작 시각
    }

    logger.info(
        f"[{name}({code})] 포지션 등록 | "
        f"매입가: {avg_price:,}원 | "
        f"안전손절: {hard_stop:,}원({STOP_LOSS_PCT:+.1f}%) | "
        f"익절가: {take_profit:,}원({TAKE_PROFIT_PCT:+.1f}%) | "
        f"MA20 손절 활성화: {MA20_STOP_LOSS['enabled']}"
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


def _is_market_close_time() -> bool:
    return datetime.now().strftime("%H:%M") >= MARKET_CLOSE


def check_position(pos: dict) -> str:
    """
    단일 포지션 체크.

    Returns:
        "ma20_stop"      : 1분봉 MA20 이탈 손절
        "hard_stop"      : 고정 손절 (안전망)
        "take_profit"    : 익절
        "market_close"   : 장마감 강제청산
        "exec_weakness"  : 체결강도 1분 약세
        "hold"           : 유지
    """
    code = pos["code"]
    info = get_current_price(code)
    if not info:
        return "hold"

    cur_price     = info["price"]
    avg_price     = pos["avg_price"]
    profit_pct    = (cur_price - avg_price) / avg_price * 100
    exec_strength = info.get("exec_strength", 100.0)

    logger.debug(
        f"[{pos['name']}] 현재가: {cur_price:,}원 | "
        f"수익률: {profit_pct:+.2f}% | "
        f"체결강도: {exec_strength:.1f}%"
    )

    # ── 장 마감 강제 청산 ──────────────────────────────────────
    if _is_market_close_time():
        logger.info(f"[{pos['name']}] ⏰ 장마감 강제청산 ({cur_price:,}원, {profit_pct:+.2f}%)")
        return "market_close"

    # ── 고정 손절 (안전망) ─────────────────────────────────────
    if cur_price <= pos["hard_stop"]:
        logger.info(f"[{pos['name']}] 🔴 고정손절 발동! {cur_price:,}원 ({profit_pct:+.2f}%)")
        return "hard_stop"

    # ── 익절 ──────────────────────────────────────────────────
    if cur_price >= pos["take_profit"]:
        logger.info(f"[{pos['name']}] 🟢 익절 발동! {cur_price:,}원 ({profit_pct:+.2f}%)")
        return "take_profit"

    # ── 1분봉 MA20 손절 ───────────────────────────────────────
    if MA20_STOP_LOSS["enabled"]:
        ma40 = get_ma40_1min(code)
        if ma40 > 0 and cur_price < ma40:
            logger.info(
                f"[{pos['name']}] 🟠 MA20 손절 발동! "
                f"현재가: {cur_price:,} < MA40: {ma40:,.0f} ({profit_pct:+.2f}%)"
            )
            return "ma20_stop"

    # ── 체결강도 1분 약세 ─────────────────────────────────────
    now = datetime.now()
    if exec_strength < 100.0:
        if pos["weak_since"] is None:
            _positions[code]["weak_since"] = now
            logger.debug(f"[{pos['name']}] 체결강도 약세 시작 ({exec_strength:.1f}%)")
        else:
            elapsed = (now - pos["weak_since"]).seconds
            logger.debug(f"[{pos['name']}] 체결강도 약세 {elapsed}초 경과")
            if elapsed >= EXEC_STRENGTH_TIMEOUT:
                logger.info(
                    f"[{pos['name']}] 🟡 체결강도 1분 약세 발동! "
                    f"{exec_strength:.1f}% | {cur_price:,}원 ({profit_pct:+.2f}%)"
                )
                return "exec_weakness"
    else:
        if pos["weak_since"] is not None:
            logger.debug(f"[{pos['name']}] 체결강도 회복 ({exec_strength:.1f}%) - 타이머 리셋")
            _positions[code]["weak_since"] = None

    return "hold"


def execute_sell(pos: dict, reason: str) -> bool:
    """매도 실행"""
    reason_map = {
        "ma20_stop"    : "MA20 손절",
        "hard_stop"    : "고정 손절",
        "take_profit"  : "익절",
        "market_close" : "장마감 청산",
        "exec_weakness": "체결강도 약세",
    }
    label  = reason_map.get(reason, reason)
    result = sell_market(pos["code"], pos["qty"])

    if result["success"]:
        logger.info(f"[{pos['name']}] ✅ {label} 매도 완료 | 주문번호: {result['order_no']}")
        remove_position(pos["code"])
        return True
    else:
        logger.error(f"[{pos['name']}] ❌ {label} 매도 실패 | {result['msg']}")
        return False


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
            info     = get_current_price(pos["code"])
            ma40     = get_ma40_1min(pos["code"])
            cur      = info.get("price", 0) if info else 0
            strength = info.get("exec_strength", 0) if info else 0
            pct      = (cur - pos["avg_price"]) / pos["avg_price"] * 100 if cur else 0
            sign     = "+" if pct >= 0 else ""
            weak_info = ""
            if pos["weak_since"]:
                elapsed = (datetime.now() - pos["weak_since"]).seconds
                weak_info = f" ⚠️ 약세{elapsed}초"

            print(f"  {pos['name']} ({pos['code']})")
            print(f"    수량: {pos['qty']:,}주 | 매입가: {pos['avg_price']:,}원 | 현재가: {cur:,}원")
            print(f"    MA40: {ma40:,.0f}원 | 고정손절: {pos['hard_stop']:,}원 | 익절가: {pos['take_profit']:,}원")
            print(f"    수익률: {sign}{pct:.2f}% | 체결강도: {strength:.1f}%{weak_info}")
    print(f"{'='*65}\n")
