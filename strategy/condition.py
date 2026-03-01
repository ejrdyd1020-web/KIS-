"""
strategy/condition.py
=====================
[업데이트 내용 - 스토캐스틱 슬로우 매수 신호 추가]
- calculate_stochastic_slow() 함수 추가
- check_stochastic_signal() 함수 추가
- check_advanced_filters() 내부에 8번 조건 추가
"""

import logging
import pandas as pd

# ──────────────────────────────────────────
# ↓↓↓ 기존 파일의 import 구문들을 여기 유지 ↓↓↓
# ──────────────────────────────────────────
# 예) from api.kiwoom import get_basic_info
# 예) from utils.utils import ...
# ──────────────────────────────────────────

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════
# [신규 추가] 스토캐스틱 슬로우 계산 함수
# ══════════════════════════════════════════
def calculate_stochastic_slow(df, k_period=12, d_period=5, smooth_period=5):
    """
    스토캐스틱 슬로우 직접 계산 (외부 라이브러리 불필요)

    계산 순서:
      1. Fast %K  = (현재종가 - 최저가N) / (최고가N - 최저가N) * 100
      2. Slow %K  = Fast %K 의 smooth_period 이동평균
      3. Slow %D  = Slow %K 의 d_period 이동평균

    Parameters:
        df            : OHLCV DataFrame (columns: high, low, close 필수)
        k_period      : Fast %K 계산 기간 (기본 12)
        smooth_period : Slow %K 평활화 기간 (기본 5)
        d_period      : Slow %D 기간       (기본 5)

    Returns:
        slow_k (Series), slow_d (Series)
    """
    low_min  = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()

    # 고가 = 저가인 경우(캔들 없음 등) 분모 0 방지
    denom  = (high_max - low_min).replace(0, float('nan'))
    fast_k = ((df['close'] - low_min) / denom) * 100

    slow_k = fast_k.rolling(window=smooth_period).mean()
    slow_d = slow_k.rolling(window=d_period).mean()

    return slow_k, slow_d


# ══════════════════════════════════════════
# [신규 추가] 스토캐스틱 신호 판단 함수
# ══════════════════════════════════════════
def check_stochastic_signal(code: str) -> str:
    """
    스토캐스틱 슬로우 기반 매수/매도/관망 신호 반환

    신호 기준:
      BUY  - 침체권(20 이하)에서 %K가 %D를 상향 돌파 (골든크로스)
             → 바닥에서 반등 시작하는 순간을 포착
      SELL - 과열권(80 이상)에서 %K가 %D를 하향 돌파 (데드크로스)
      HOLD - 그 외 모든 경우

    Returns:
        "BUY" | "SELL" | "HOLD"
    """
    try:
        from api.chart import get_minute_chart  # 순환 참조 방지

        candles = get_minute_chart(code, count=100)
        if len(candles) < 25:
            return "HOLD"

        df = pd.DataFrame(candles)
        df = df.iloc[::-1].reset_index(drop=True)  # 최신이 마지막 행으로

        slow_k, slow_d = calculate_stochastic_slow(df)

        last_k = slow_k.iloc[-1]
        last_d = slow_d.iloc[-1]
        prev_k = slow_k.iloc[-2]
        prev_d = slow_d.iloc[-2]

        # NaN 체크 (데이터 부족 시 HOLD)
        if any(pd.isna(v) for v in [last_k, last_d, prev_k, prev_d]):
            return "HOLD"

        # ── 매수: 침체권(20 이하)에서 골든크로스 ──────────────────
        # K와 D 중 하나라도 20 이하였고, 이번 봉에서 K가 D를 상향 돌파
        if last_k > last_d and prev_k <= prev_d and min(prev_k, prev_d) <= 20:
            return "BUY"

        # ── 매도: 과열권(80 이상)에서 데드크로스 ──────────────────
        # K와 D 중 하나라도 80 이상이었고, 이번 봉에서 K가 D를 하향 돌파
        if last_k < last_d and prev_k >= prev_d and max(prev_k, prev_d) >= 80:
            return "SELL"

    except Exception as e:
        logger.error(f"Stochastic 에러({code}): {e}")

    return "HOLD"


# ══════════════════════════════════════════
# 기존 함수들 (변경 없이 유지)
# ══════════════════════════════════════════

# ──────────────────────────────────────────
# ↓↓↓ 기존 check_basic_filters() 등 함수들을 여기 유지 ↓↓↓
# ──────────────────────────────────────────


def check_advanced_filters(code: str, basic: dict) -> tuple[bool, list[str]]:
    """
    고급 필터 조건 검사
    [기존 1~7번 필터 유지 + 8번 스토캐스틱 조건 추가]
    """
    passed = []
    failed = []

    # ──────────────────────────────────────
    # ↓↓↓ 기존 필터 1~7번 코드를 여기 유지 ↓↓↓
    # ──────────────────────────────────────

    # ── [8번 신규] 스토캐스틱 매수 신호 ──────────────────────────
    stoch_sig = check_stochastic_signal(code)
    if stoch_sig == "BUY":
        passed.append("스토캐스틱(침체탈출)")
    else:
        # HOLD / SELL 구분해서 로그에 남김
        failed.append(f"스토캐스틱(관망/{stoch_sig})")
    # ─────────────────────────────────────────────────────────────

    all_passed = len(failed) == 0
    return all_passed, passed
