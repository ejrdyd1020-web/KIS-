# ============================================================
#  api/index.py  –  시장 국면 판단
#
#  코스피 / 코스닥 일봉 MA5, MA20 조회
#  MA5 > MA20  → 상승추세  → BULL  → BREAKOUT 80% / REVERSION 20%
#  MA5 <= MA20 → 보합/하락 → BEAR  → BREAKOUT 20% / REVERSION 80%
#
#  KIS API: FHKUP03500100 (지수 기간별 시세)
# ============================================================

import requests
import sys
import os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from auth         import get_headers, get_base_url
from utils.logger import get_logger
from config       import TOTAL_BUDGET, MARKET_PHASE

logger = get_logger("index")

# 시장 국면 상수
BULL = "BULL"
BEAR = "BEAR"


# ══════════════════════════════════════════
# 지수 일봉 데이터 조회
# ══════════════════════════════════════════

def get_index_daily(index_code: str, count: int = 25) -> list[dict]:
    """
    지수 일봉 데이터 조회.

    Args:
        index_code: "0001" = 코스피 / "1001" = 코스닥
        count     : 조회 일수 (MA20 계산에 최소 21일 필요)

    Returns:
        [{"date", "close"}, ...]  최신 데이터가 index 0
    """
    tr_id = "FHKUP03500100"
    today = datetime.now().strftime("%Y%m%d")

    params = {
        "fid_cond_mrkt_div_code": "U",          # U = 지수
        "fid_input_iscd"        : index_code,
        "fid_input_date_1"      : "20200101",
        "fid_input_date_2"      : today,
        "fid_period_div_code"   : "D",           # D = 일봉
        "fid_org_adj_prc"       : "0",
    }

    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
            headers=get_headers(tr_id),
            params=params,
            timeout=5,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"[지수 {index_code}] 일봉 조회 실패: {data.get('msg1')}")
            return []

        candles = []
        for item in data.get("output2", [])[:count]:
            close = float(item.get("bstp_nmix_prpr", 0))
            if close > 0:
                candles.append({
                    "date" : item.get("stck_bsop_date", ""),
                    "close": close,
                })
        return candles

    except Exception as e:
        logger.error(f"[지수 {index_code}] 일봉 조회 오류: {e}")
        return []


# ══════════════════════════════════════════
# MA 계산
# ══════════════════════════════════════════

def _calc_ma(candles: list[dict], period: int) -> float | None:
    """이동평균 계산. 데이터 부족 시 None."""
    if len(candles) < period:
        return None
    closes = [c["close"] for c in candles[:period]]
    return round(sum(closes) / period, 2)


# ══════════════════════════════════════════
# 시장 국면 판단
# ══════════════════════════════════════════

def get_market_phase() -> str:
    """
    코스피 + 코스닥 일봉 MA5/MA20 기준 시장 국면 판단.

    판단 로직:
      - 코스피 MA5 > MA20  AND  코스닥 MA5 > MA20 → BULL (둘 다 상승)
      - 그 외                                      → BEAR (하나라도 보합/하락)
      - API 실패 시 → BEAR (보수적 기본값)

    Returns:
        "BULL" or "BEAR"
    """
    ma5_period  = MARKET_PHASE.get("ma_short", 5)
    ma20_period = MARKET_PHASE.get("ma_long",  20)
    need        = ma20_period + 1   # 최소 21일치

    results = {}

    for name, code in [("코스피", "0001"), ("코스닥", "1001")]:
        candles = get_index_daily(code, count=need)
        if not candles:
            logger.warning(f"{name} 데이터 없음 → BEAR 처리")
            return BEAR

        ma5  = _calc_ma(candles, ma5_period)
        ma20 = _calc_ma(candles, ma20_period)

        if ma5 is None or ma20 is None:
            logger.warning(f"{name} MA 계산 불가 → BEAR 처리")
            return BEAR

        is_bull = ma5 > ma20
        results[name] = {
            "ma5"    : ma5,
            "ma20"   : ma20,
            "is_bull": is_bull,
        }
        logger.info(
            f"{name} | MA{ma5_period}: {ma5:,.2f} / MA{ma20_period}: {ma20:,.2f} "
            f"→ {'📈 상승' if is_bull else '📉 보합/하락'}"
        )

    # 둘 다 상승이어야 BULL
    if all(v["is_bull"] for v in results.values()):
        logger.info("🟢 시장 국면: BULL (코스피 + 코스닥 모두 상승추세)")
        return BULL
    else:
        logger.info("🔴 시장 국면: BEAR (하나 이상 보합/하락)")
        return BEAR


# ══════════════════════════════════════════
# 전략별 자금 배분 계산
# ══════════════════════════════════════════

def calc_strategy_budget(phase: str) -> dict:
    """
    시장 국면에 따른 전략별 배분 금액 계산.

    Args:
        phase: "BULL" or "BEAR"

    Returns:
        {
            "breakout_total" : int,   # BREAKOUT 전략 총 배분 금액
            "reversion_total": int,   # REVERSION 전략 총 배분 금액
            "phase"          : str,
        }

    예시 (TOTAL_BUDGET=10,000,000):
        BULL → breakout: 8,000,000  / reversion: 2,000,000
        BEAR → breakout: 2,000,000  / reversion: 8,000,000
    """
    bull_ratio = MARKET_PHASE.get("bull_breakout_ratio", 0.8)   # BULL 시 BREAKOUT 비율
    bear_ratio = MARKET_PHASE.get("bear_breakout_ratio", 0.2)   # BEAR 시 BREAKOUT 비율

    breakout_ratio  = bull_ratio if phase == BULL else bear_ratio
    reversion_ratio = 1.0 - breakout_ratio

    breakout_total  = int(TOTAL_BUDGET * breakout_ratio)
    reversion_total = int(TOTAL_BUDGET * reversion_ratio)

    logger.info(
        f"💰 자금 배분 [{phase}] | "
        f"BREAKOUT: {breakout_total:,}원 ({breakout_ratio*100:.0f}%) / "
        f"REVERSION: {reversion_total:,}원 ({reversion_ratio*100:.0f}%)"
    )

    return {
        "breakout_total" : breakout_total,
        "reversion_total": reversion_total,
        "phase"          : phase,
    }


def calc_position_budgets(scores: list[float], total_budget: int) -> list[int]:
    """
    점수 비율 기반 종목별 자금 배분.

    Args:
        scores      : 각 종목의 점수 리스트 (순위순 정렬된 상태)
        total_budget: 해당 전략 총 배분 금액

    Returns:
        각 종목별 배분 금액 리스트

    예시:
        scores=[70, 50, 30], total=6,000,000
        → 합계=150
        → [2,800,000, 2,000,000, 1,200,000]
    """
    total_score = sum(scores)
    if total_score <= 0:
        # 점수 합이 0이면 균등 배분
        n = len(scores)
        return [total_budget // n] * n if n > 0 else []

    budgets = []
    for score in scores:
        amt = int(total_budget * score / total_score)
        budgets.append(amt)

    return budgets
