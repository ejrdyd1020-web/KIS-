# ============================================================
#  monitor/position.py
#  [우선순위] 고정손절 > MA120이탈 > 트레일링스탑 > 스토캐스틱 > 장마감
# ============================================================

import logging
from datetime import datetime
from strategy.condition import check_stochastic_signal, get_ma120  # MA120 호출 추가
from api.price import get_current_price
from api.order import sell_market
from config import STOP_LOSS_PCT, MARKET_CLOSE

logger = logging.getLogger(__name__)

# 장마감 관련 상수
FORCE_SELL_TIME = "15:20"

def add_position(code: str, name: str, qty: int, avg_price: int):
    """매수 완료 후 포지션 등록 (트레일링 스탑용 max_price 필드 추가)"""
    # [버그수정] 손절가는 매수가보다 낮아야 하므로 (1 - PCT) 사용
    hard_stop = int(avg_price * (1 - abs(STOP_LOSS_PCT) / 100))
    
    _positions[code] = {
        "code": code,
        "name": name,
        "qty": qty,
        "avg_price": avg_price,
        "max_price": avg_price,  # 실시간 고점 추적용
        "hard_stop": hard_stop,
    }
    logger.info(f"[{name}] 포지션 등록 (손절가: {hard_stop:,}원)")

def check_position(pos: dict) -> str:
    """
    [확정 우선순위]
    1. 고정 손절 (-3%) -> 가장 중요
    2. MA120 이탈     -> 추세 붕괴 대응
    3. 트레일링 스탑  -> 수익 보존
    4. 스토캐스틱     -> 기술적 매도
    5. 장마감 (15:20) -> 최종 청산
    """
    code = pos["code"]
    info = get_current_price(code)
    if not info: return "hold"

    cur_price = info["price"]
    avg_price = pos["avg_price"]
    now = datetime.now().strftime("%H:%M")

# 최고가 갱신 (트레일링 스탑 기준)
    if cur_price > pos.get("max_price", 0):
        pos["max_price"] = cur_price

    # ── [1순위] 고정 손절 ──────────────────────────────────────
    if cur_price <= pos["hard_stop"]:
        logger.warning(f"[{pos['name']}] ❗ 고정 손절선 이탈")
        return "hard_stop"

    # ── [2순위] MA120 이탈 (하락장 전환) ────────────────────────
    ma120 = get_ma120(code)
    if ma120 and cur_price < ma120:
        logger.info(f"[{pos['name']}] 📉 MA120 아래로 추세 붕괴")
        return "ma120_stop"

    # ── [3순위] 트레일링 스탑 (수익 3% 이상 시 작동) ──────────────
    profit_pct = (cur_price - avg_price) / avg_price * 100
    if profit_pct >= 3.0:
        drop_pct = (pos["max_price"] - cur_price) / pos["max_price"] * 100
        if drop_pct >= 2.0: # 고점 대비 2% 하락 시
            return "trailing_stop"

    # ── [4순위] 스토캐스틱 매도 신호 ─────────────────────────────
    if check_stochastic_signal(code) == "SELL":
        return "stoch_sell"

    # ── [5순위] 장마감 강제매도 ──────────────────────────────────
    if now >= FORCE_SELL_TIME:
        return "market_close"

    return "hold"

def execute_sell(pos: dict, reason: str) -> bool:
    """매도 실행 및 사유 기록"""
    reason_map = {
        "hard_stop": "고정 손절",
        "ma120_stop": "MA120 이탈",
        "trailing_stop": "트레일링 익절",
        "stoch_sell": "스토캐스틱 매도",
        "market_close": "장마감 강제매도"
    }
    
    logger.info(f"[{pos['name']}] 매도 사유: {reason_map.get(reason, reason)}")
    result = sell_market(pos["code"], pos["qty"])
    if result["success"]:
        del _positions[pos["code"]]
        return True
    return False
