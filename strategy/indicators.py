# ============================================================
#  strategy/indicators.py  --  공통 기술 지표 모듈
#
#  strategy_reversion.py와 backtest_compare.py가 동일 로직을
#  공유하여 코드 불일치 방지.
# ============================================================

import pandas as pd


def calc_stochastic_slow(df: pd.DataFrame,
                         k_period: int = 12,
                         smooth_period: int = 5,
                         d_period: int = 5):
    """
    스토캐스틱 슬로우 계산.

    Args:
        df: OHLCV DataFrame (open/high/low/close/volume 컬럼 필수)
        k_period    : Fast-K 계산 기간 (기본 12)
        smooth_period: Fast-K → Slow-K 스무딩 기간 (기본 5)
        d_period    : Slow-K → Slow-D 기간 (기본 5)

    Returns:
        (slow_k, slow_d): pd.Series 쌍
    """
    low_min  = df["low"].rolling(window=k_period).min()
    high_max = df["high"].rolling(window=k_period).max()
    denom    = (high_max - low_min).replace(0, float("nan"))
    fast_k   = ((df["close"] - low_min) / denom) * 100
    slow_k   = fast_k.rolling(window=smooth_period).mean()
    slow_d   = slow_k.rolling(window=d_period).mean()
    return slow_k, slow_d


def build_5min_candles(candles_1m: list[dict]) -> list[dict]:
    """
    1분봉 리스트를 5분봉으로 합산.

    Args:
        candles_1m: get_minute_chart() 반환 형식 (최신봉이 앞)
                    각 원소: {time, open, high, low, close, volume}

    Returns:
        5분봉 리스트 (최신봉이 앞, 5개씩 묶음)
    """
    if len(candles_1m) < 5:
        return []

    candles_5m = []
    for i in range(0, len(candles_1m) - 4, 5):
        group = candles_1m[i:i + 5]
        candles_5m.append({
            "time"  : group[0]["time"],          # 그룹 내 가장 최신 시간
            "open"  : group[-1]["open"],          # 그룹 내 가장 오래된 봉의 시가
            "high"  : max(c["high"]   for c in group),
            "low"   : min(c["low"]    for c in group),
            "close" : group[0]["close"],          # 그룹 내 가장 최신 봉의 종가
            "volume": sum(c["volume"] for c in group),
        })
    return candles_5m
