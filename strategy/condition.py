# ============================================================
#  strategy/condition.py  –  고급 조건 검색 + 자동 매수 전략
#
#  필터 조건:
#    1. 등락률 7~25%
#    2. 전일 거래대금 300억 이상
#    3. 거래량 급증 (전일 2배 OR 1분봉 500%)
#    4. 시가총액 100억 이상
#    5. 체결강도 100% 이상
#    6. 스토캐스틱 슬로우 매수 신호 (침체권 골든크로스)
#    7. 1분봉 MA120 상승장 확인
#
#  ※ 제거된 조건:
#    - 52주 신고가 98% → 후보 종목 너무 적음
#    - 매수호가 55%    → API 오류 시 무조건 탈락
#
#  최종 3종목 선정 기준 (점수제):
#    - 거래량 급증도 40% + 등락률 30% + 체결강도 30%
# ============================================================

import time
import sys
import os
import pandas as pd
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from api.price    import get_fluctuation_rank, get_current_price
from api.chart    import get_volume_ratio_1min
from api.order    import buy_market, sell_market, calc_buy_qty
from api.balance  import get_deposit
from strategy.position import add_position, get_positions, remove_position
from utils.logger import get_logger
from config import (
    ORDER_AMOUNT,
    MAX_POSITIONS,
    CONDITION,
    ADVANCED_FILTER,
    CONDITION_SCAN_END,
    MARKET_OPEN,
    MA120_MARKET_FILTER,
)

logger = get_logger("strategy")

_bought_codes: set[str] = set()


# ══════════════════════════════════════════
# 1분봉 MA120 시장 국면 판단
# ══════════════════════════════════════════

def get_ma120(code: str) -> float | None:
    """
    해당 종목의 1분봉 120이평 값을 반환.
    KIS API 1회 한계를 넘기 위해 get_minute_chart_bulk로 다중 호출.
    데이터 부족 시 None 반환.
    """
    try:
        from api.chart import get_minute_chart_bulk
        candles = get_minute_chart_bulk(code, need=125)
        if len(candles) < 120:
            logger.warning(f"[{code}] MA120 계산 불가: 캔들 {len(candles)}개 (최소 120개 필요)")
            return None

        df = pd.DataFrame(candles)
        df = df.iloc[::-1].reset_index(drop=True)
        ma120 = df['close'].rolling(window=120).mean().iloc[-1]
        return float(ma120) if not pd.isna(ma120) else None

    except Exception as e:
        logger.error(f"[{code}] MA120 조회 오류: {e}")
        return None


def check_market_phase(code: str, current_price: float) -> bool:
    """
    1분봉 MA120 기준으로 시장 국면 판단.
    Returns:
        True  → 상승장 (현재가 >= MA120)
        False → 하락장 (현재가 < MA120)
    """
    if not MA120_MARKET_FILTER.get("enabled", True):
        return True

    ma120 = get_ma120(code)
    if ma120 is None:
        logger.warning(f"[{code}] MA120 확인 불가 → 상승장으로 간주")
        return True

    is_bull = current_price >= ma120
    logger.debug(f"[{code}] 현재가={current_price:,} / MA120={ma120:,.1f} → {'상승장' if is_bull else '하락장'}")
    return is_bull


def liquidate_single_position(code: str, info: dict, reason: str = "MA120 이탈"):
    """해당 종목 1개만 즉시 시장가 전량 매도. 다른 보유 종목에는 영향 없음."""
    qty  = info.get("qty", 0)
    name = info.get("name", code)
    if qty <= 0:
        return
    try:
        result = sell_market(code, qty)
        if result.get("success"):
            remove_position(code)
            logger.info(f"[{name}({code})] ✅ MA120 이탈 손절 완료 ({qty}주)")
        else:
            logger.error(f"[{name}({code})] ❌ 손절 실패: {result.get('msg')}")
    except Exception as e:
        logger.error(f"[{name}({code})] 손절 중 오류: {e}")


def monitor_market_phase():
    """보유 종목별 MA120 이탈 여부 개별 감시. 이탈 종목만 즉시 손절."""
    positions = get_positions()
    if not positions:
        return

    for code, info in list(positions.items()):
        price_info = get_current_price(code)
        if not price_info:
            continue
        current_price = price_info.get("price", 0)
        if current_price <= 0:
            continue

        is_bull = check_market_phase(code, current_price)
        name    = info.get("name", code)

        if not is_bull:
            logger.warning(f"🔴 [{name}({code})] MA120 이탈 → 해당 종목만 즉시 손절")
            liquidate_single_position(code, info, reason="MA120 이탈")
        else:
            logger.debug(f"[{name}({code})] MA120 상승장 유지 ✅")


# ══════════════════════════════════════════
# 스토캐스틱 슬로우 계산
# ══════════════════════════════════════════

def calculate_stochastic_slow(df, k_period=12, d_period=5, smooth_period=5):
    low_min  = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()
    denom    = (high_max - low_min).replace(0, float('nan'))
    fast_k   = ((df['close'] - low_min) / denom) * 100
    slow_k   = fast_k.rolling(window=smooth_period).mean()
    slow_d   = slow_k.rolling(window=d_period).mean()
    return slow_k, slow_d


def check_stochastic_signal(code: str) -> str:
    """
    스토캐스틱 슬로우 기반 매수/매도/관망 신호 반환
      BUY  - 침체권(20↓)에서 골든크로스
      SELL - 과열권(80↑)에서 데드크로스
      HOLD - 그 외
    """
    try:
        from api.chart import get_minute_chart
        candles = get_minute_chart(code, count=100)
        if len(candles) < 25:
            return "HOLD"

        df = pd.DataFrame(candles)
        df = df.iloc[::-1].reset_index(drop=True)

        slow_k, slow_d = calculate_stochastic_slow(df)
        last_k = slow_k.iloc[-1]
        last_d = slow_d.iloc[-1]
        prev_k = slow_k.iloc[-2]
        prev_d = slow_d.iloc[-2]

        if any(pd.isna(v) for v in [last_k, last_d, prev_k, prev_d]):
            return "HOLD"

        if last_k > last_d and prev_k <= prev_d and min(prev_k, prev_d) <= 20:
            return "BUY"
        if last_k < last_d and prev_k >= prev_d and max(prev_k, prev_d) >= 80:
            return "SELL"

    except Exception as e:
        logger.error(f"Stochastic 에러({code}): {e}")

    return "HOLD"


# ══════════════════════════════════════════
# 조건 검색
# ══════════════════════════════════════════

def is_scan_time() -> bool:
    now = datetime.now().strftime("%H:%M")
    return MARKET_OPEN <= now <= CONDITION_SCAN_END


def check_advanced_filters(code: str, basic: dict) -> tuple[bool, list[str]]:
    """
    고급 필터 체크
      1. 당일 과열 제외 (25% 미만)
      2. 전일 거래대금 300억 이상
      3. 거래량 급증 (전일 2배 OR 1분봉 500%)
      4. 시가총액 100억 이상
      5. 체결강도 100% 이상
      6. 스토캐스틱 골든크로스 (침체권)
      7. 1분봉 MA120 상승장 확인
    """
    passed = []
    failed = []

    detail = get_current_price(code)
    if not detail:
        return False, []

    price       = basic.get("price", 0)
    volume      = basic.get("volume", 0)
    change_rate = basic.get("change_rate", 0)

    # ── 1. 당일 과열 제외 ─────────────────────────────────────
    if change_rate >= ADVANCED_FILTER["max_day_change_rate"]:
        failed.append(f"과열({change_rate:.1f}%)")
    else:
        passed.append(f"등락률정상({change_rate:.1f}%)")

    # ── 2. 전일 거래대금 300억 이상 ───────────────────────────
    prev_trade_amount = int(basic.get("prev_trade_amount", 0) / 100_000_000)
    if prev_trade_amount >= ADVANCED_FILTER["min_trade_amount"]:
        passed.append(f"전일거래대금({prev_trade_amount:,}억)")
    else:
        failed.append(f"전일거래대금부족({prev_trade_amount:,}억)")

    # ── 3. 거래량 급증 (OR 조건) ──────────────────────────────
    vol_ratio_1min = get_volume_ratio_1min(code)
    prev_vol       = detail.get("prev_volume", 0)
    surge_ratio    = volume / prev_vol if prev_vol > 0 else 0

    vol_1min_ok  = vol_ratio_1min >= ADVANCED_FILTER["min_volume_ratio_1min"]
    vol_daily_ok = surge_ratio    >= ADVANCED_FILTER["volume_surge_ratio"]

    if vol_1min_ok and vol_daily_ok:
        passed.append(f"거래량급증(전일{surge_ratio:.1f}배+1분봉{vol_ratio_1min:.0f}%)")
    elif vol_1min_ok:
        passed.append(f"거래량급증(1분봉{vol_ratio_1min:.0f}%)")
    elif vol_daily_ok:
        passed.append(f"거래량급증(전일{surge_ratio:.1f}배)")
    elif vol_ratio_1min == 0.0:
        passed.append("거래량급증(확인불가-통과)")
    else:
        failed.append(f"거래량부족(전일{surge_ratio:.1f}배/1분봉{vol_ratio_1min:.0f}%)")

    # ── 4. 시가총액 100억 이상 ────────────────────────────────
    market_cap = detail.get("market_cap", 0)
    if market_cap > 0:
        if market_cap >= ADVANCED_FILTER["min_market_cap"]:
            passed.append(f"시가총액({market_cap:,}억)")
        else:
            failed.append(f"시가총액미달({market_cap:,}억)")
    else:
        passed.append("시가총액(확인불가-통과)")

    # ── 5. 체결강도 100% 이상 ────────────────────────────────
    execution_strength = detail.get("exec_strength", 0.0)
    if execution_strength > 0:
        if execution_strength >= ADVANCED_FILTER["min_execution_strength"]:
            passed.append(f"체결강도({execution_strength:.1f}%)")
        else:
            failed.append(f"체결강도부족({execution_strength:.1f}%)")
    else:
        passed.append("체결강도(확인불가-통과)")

    # ── 6. 스토캐스틱 골든크로스 ─────────────────────────────
    stoch_sig = check_stochastic_signal(code)
    if stoch_sig == "BUY":
        passed.append("스토캐스틱(침체탈출)")
    else:
        failed.append(f"스토캐스틱(관망/{stoch_sig})")

    # ── 7. 1분봉 MA120 상승장 확인 ───────────────────────────
    is_bull = check_market_phase(code, price)
    if is_bull:
        passed.append("MA120(상승장)")
    else:
        failed.append("MA120(하락장-진입불가)")

    all_passed = len(failed) == 0
    if failed:
        logger.debug(f"[{basic.get('name')}] 탈락: {', '.join(failed)}")

    return all_passed, passed


def filter_candidates(stocks: list[dict]) -> list[dict]:
    """기본 + 고급 필터 적용"""
    positions  = get_positions()
    candidates = []

    for s in stocks:
        code = s["code"]

        if code in _bought_codes or code in positions:
            continue

        price = s["price"]
        if not (CONDITION["min_price"] <= price <= CONDITION["max_price"]):
            continue
        if s["volume"] < CONDITION["min_volume"]:
            continue
        rate = s["change_rate"]
        if not (CONDITION["min_change_rate"] <= rate <= CONDITION["max_change_rate"]):
            continue

        ok, passed_list = check_advanced_filters(code, s)
        if ok:
            s["passed_filters"] = passed_list
            candidates.append(s)
            logger.info(f"[{s['name']}] ✅ 조건 통과: {', '.join(passed_list)}")

        time.sleep(0.3)

    logger.info(f"조건 검색: {len(stocks)}개 → 필터 후 {len(candidates)}개 후보")
    return candidates


def score_candidate(stock: dict) -> float:
    """
    최종 종목 선정 점수 계산
      - 거래량 급증도 40% : 가장 중요한 모멘텀 지표
      - 등락률        30% : 상승 강도
      - 체결강도      30% : 매수세 강도
    """
    rate          = stock.get("change_rate", 0)
    volume        = stock.get("volume", 0)
    exec_strength = stock.get("exec_strength", 100.0)

    rate_score     = min(rate / 25.0, 1.0) * 100
    volume_score   = min(volume / 1_000_000, 1.0) * 100
    strength_score = min(exec_strength / 200.0, 1.0) * 100

    score = (volume_score * 0.4) + (rate_score * 0.3) + (strength_score * 0.3)
    return round(score, 2)


def execute_buy(stock: dict) -> bool:
    """매수 실행"""
    code  = stock["code"]
    name  = stock["name"]
    price = stock["price"]

    if len(get_positions()) >= MAX_POSITIONS:
        logger.info("최대 보유 종목 수 도달 - 매수 스킵")
        return False

    deposit = get_deposit()
    budget  = min(ORDER_AMOUNT, deposit - 10_000)
    if budget <= 0:
        logger.warning(f"예수금 부족 (예수금: {deposit:,}원)")
        return False

    qty = calc_buy_qty(price, budget)
    if qty <= 0:
        logger.warning(f"[{name}] 매수 수량 0 - 스킵")
        return False

    logger.info(f"[{name}({code})] 매수 시도 | {qty}주 × {price:,}원")
    result = buy_market(code, qty)

    if result["success"]:
        add_position(code, name, qty, price)
        _bought_codes.add(code)
        logger.info(f"[{name}] ✅ 매수 완료")
        return True
    else:
        logger.warning(f"[{name}] ❌ 매수 실패: {result['msg']}")
        return False


def run_strategy(stop_event=None):
    """조건 검색 + 매수 전략 루프 (30초마다)"""
    SCAN_INTERVAL = 30
    logger.info("🔍 조건 검색 전략 시작 (1분봉 MA120 개별 종목 손절 필터 활성)")

    while True:
        if stop_event and stop_event.is_set():
            logger.info("조건 검색 전략 종료")
            break

        if not is_scan_time():
            time.sleep(60)
            continue

        # ── Step 1: 보유 종목별 MA120 이탈 감시 (개별 손절) ──
        monitor_market_phase()

        if len(get_positions()) >= MAX_POSITIONS:
            time.sleep(SCAN_INTERVAL)
            continue

        # ── Step 2: watchlist 우선 감시 (장 전 선정 종목) ────
        from premarket import load_watchlist
        watchlist = load_watchlist()
        if watchlist:
            wl_codes = {s["code"] for s in watchlist}
            logger.info(f"📋 watchlist {len(watchlist)}개 종목 우선 감시 중")
        else:
            wl_codes = set()

        # ── Step 3: 조건 검색 + 상위 3종목 매수 ─────────────
        stocks = get_fluctuation_rank(top_n=30)

        # watchlist 종목을 맨 앞으로 정렬
        if wl_codes:
            stocks.sort(key=lambda x: (0 if x["code"] in wl_codes else 1))
        if not stocks:
            time.sleep(SCAN_INTERVAL)
            continue

        candidates = filter_candidates(stocks)
        if not candidates:
            logger.info("매수 후보 없음")
            time.sleep(SCAN_INTERVAL)
            continue

        candidates.sort(key=lambda x: score_candidate(x), reverse=True)

        for stock in candidates:
            if len(get_positions()) >= MAX_POSITIONS:
                break
            execute_buy(stock)
            time.sleep(1)

        time.sleep(SCAN_INTERVAL)


def print_candidates():
    """조건 검색 결과 출력"""
    print("\n[🔍 조건 검색 실행 중...]\n")
    stocks = get_fluctuation_rank(top_n=30)
    if not stocks:
        print("  등락률 순위 조회 실패")
        return

    candidates = filter_candidates(stocks)
    if not candidates:
        print("  매수 후보 없음")
        return

    candidates.sort(key=lambda x: score_candidate(x), reverse=True)

    print(f"{'='*80}")
    print(f"  🔍 매수 후보 종목 ({len(candidates)}개) — 상위 3개 매수 대상")
    print(f"{'='*80}")
    print(f"  {'점수':>5} {'종목명':<14} {'현재가':>8} {'등락률':>7} {'거래대금':>8}  통과 조건")
    print(f"  {'-'*75}")
    for i, s in enumerate(candidates[:10]):
        score        = score_candidate(s)
        trade_amount = int(s['price'] * s['volume'] / 100_000_000)
        filters      = ", ".join(s.get("passed_filters", []))
        marker       = "★" if i < 3 else " "
        print(f"  {marker}{score:>5.1f} {s['name']:<14} {s['price']:>8,} "
              f"{s['change_rate']:>+6.2f}% {trade_amount:>7,}억  {filters}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    from auth import get_access_token
    get_access_token()
    print_candidates()
