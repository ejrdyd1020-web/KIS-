# ============================================================
#  strategy/strategy_reversion.py  –  전략 B: REVERSION
#
#  운용 시간: 09:10 ~ 15:20
#
#  핵심 타점: 스토캐스틱 침체권 골든크로스 + 이평 정배열
#
#  매수 조건 (멀티 타임프레임):
#    [1차 관문] 1분봉 스토캐스틱 침체권 골든크로스 (K<20 → K>D, 3봉 이내)
#    [2차 관문] 5분봉 추세 동시 확인
#              - 현재가 >= 5분봉 MA20 (상승 추세)
#              - 5분봉 스토캐스틱 K < 50 (상위 TF 과열 아님)
#    [3차 필터]
#    1. 전일 거래대금 300억 이상
#    2. 거래량 급증 (전일 2배 이상)
#    3. 시가총액 100억 이상
#  종목 우선순위: 거래대금 70% + 거래량배율 30%
#
#  손절 / 익절:
#    - 고정 손절  : -1.5% (R:R = 1:2)
#    - 고정 익절  : +3.0% (짧은 순환매)
#    - 트레일링   : 수익 2% 이상 시 작동, 고점 대비 -1.5%
#
#  스캔 주기: 30초
# ============================================================

import time
import sys
import os
import pandas as pd
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from premarket             import load_watchlist
from api.price             import get_current_price, get_volume_rank
from api.chart             import get_volume_ratio_1min, get_minute_chart, get_minute_chart_bulk, get_5min_chart
from api.order             import buy_market, calc_buy_qty
from api.balance           import get_deposit
from strategy.position     import (
    add_position, get_positions,
    is_daily_loss_halted, is_buyable_time,
)
from utils.logger          import get_logger
from config import (
    MAX_POSITIONS, ORDER_AMOUNT,
    REVERSION, CONDITION, ADVANCED_FILTER, MA120_MARKET_FILTER, MARKET_PHASE,
    STRATEGY_REVERSION,
)

try:
    from api.index import calc_position_budgets
except ImportError:
    from index import calc_position_budgets

logger = get_logger("reversion")


# ══════════════════════════════════════════
# 시간 체크
# ══════════════════════════════════════════

def is_reversion_time() -> bool:
    """09:10 ~ 15:20 구간 여부"""
    now = datetime.now().strftime("%H:%M")
    return REVERSION["start_time"] <= now < REVERSION["end_time"]


# ══════════════════════════════════════════
# MA120 시장 국면 판단
# ══════════════════════════════════════════

def get_ma120(code: str) -> float | None:
    """1분봉 120이평 계산. 데이터 부족 시 None."""
    try:
        candles = get_minute_chart_bulk(code, need=125)
        if len(candles) < 120:
            return None
        df    = pd.DataFrame(candles).iloc[::-1].reset_index(drop=True)
        ma120 = df["close"].rolling(window=120).mean().iloc[-1]
        return float(ma120) if not pd.isna(ma120) else None
    except Exception as e:
        logger.error(f"[{code}] MA120 오류: {e}")
        return None


def check_market_phase(code: str, price: float) -> bool:
    """현재가 >= MA120 이면 상승장(True)"""
    if not MA120_MARKET_FILTER.get("enabled", True):
        return True
    ma120 = get_ma120(code)
    if ma120 is None:
        logger.warning(f"[{code}] MA120 확인 불가 → 하락장으로 간주(Fail-safe)")
        return False
    is_bull = price >= ma120
    logger.debug(f"[{code}] 현재가={price:,} / MA120={ma120:,.1f} → {'상승장' if is_bull else '하락장'}")
    return is_bull


# ══════════════════════════════════════════
# 5분봉 추세 + 스토캐스틱 확인
# ══════════════════════════════════════════

def check_5min_trend(code: str, price: float) -> tuple[bool, str]:
    """
    5분봉 기준 추세 확인 (MA120 상승장 필터 대체).

      조건 1: 현재가 >= 5분봉 MA20  (단기 상승 추세)
      조건 2: 5분봉 스토캐스틱 K < 50  (상위 TF 과열 아님 → 진입 여력)

    Returns:
        (통과여부, 설명 문자열)
    """
    try:
        candles = get_5min_chart(code, need=30)   # MA20(20봉) + 스토캐스틱(12+5+5)용
        if len(candles) < 22:
            logger.warning(f"[{code}] 5분봉 데이터 부족({len(candles)}개) → 차단")
            return False, "5분봉(데이터부족-차단)"

        df = pd.DataFrame(candles).iloc[::-1].reset_index(drop=True)  # 과거→최신 정렬

        # ── MA20 ───────────────────────────────────────────────
        ma20 = df["close"].rolling(window=20).mean().iloc[-1]
        if pd.isna(ma20):
            return False, "5분봉MA20(산출불가-차단)"
        above_ma20 = price >= ma20

        # ── 스토캐스틱 K ───────────────────────────────────────
        slow_k, _ = _calc_stochastic_slow(df)
        cur_k = slow_k.iloc[-1]
        if pd.isna(cur_k):
            return False, "5분봉스토캐스틱(산출불가-차단)"
        k_not_overbought = cur_k < 65   # 50 → 65, 횡보장 대응

        if above_ma20 and k_not_overbought:
            return True, f"5분봉(MA20상승+K={cur_k:.0f})"
        elif not above_ma20:
            return False, f"5분봉MA20하락({price:,}<{ma20:,.0f})"
        else:
            return False, f"5분봉K과열({cur_k:.0f}≥50)"

    except Exception as e:
        logger.error(f"[{code}] 5분봉 추세 확인 오류: {e}")
        return False, "5분봉(오류-차단)"


# ══════════════════════════════════════════
# 스토캐스틱 슬로우
# ══════════════════════════════════════════

from strategy.indicators import calc_stochastic_slow as _calc_stochastic_slow_fn

def _calc_stochastic_slow(df, k_period=12, d_period=5, smooth_period=5):
    return _calc_stochastic_slow_fn(df, k_period=k_period,
                                    smooth_period=smooth_period,
                                    d_period=d_period)


def check_stochastic_signal(code: str, cross_window: int = 3) -> str:
    """
    스토캐스틱 슬로우 매수/매도/관망 신호.
      BUY  : 최근 cross_window 봉 이내 침체권(20↓) 골든크로스 발생
      SELL : 과열권(80↑) 데드크로스
      HOLD : 그 외

    cross_window: 골든크로스 감지 허용 봉 수 (기본 3봉)
      - 1봉: 직전 봉에서만 크로스 감지 (기존 방식, 포착률 낮음)
      - 3봉: 최근 3봉 이내 크로스 감지 (30초 스캔 주기에서 놓친 크로스 보완)
      ※ K값이 이미 40 이상이면 이미 반등 많이 된 것으로 간주 → HOLD
    """
    try:
        candles = get_minute_chart(code, count=100)
        if len(candles) < 25:
            return "HOLD"
        df = pd.DataFrame(candles).iloc[::-1].reset_index(drop=True)
        slow_k, slow_d = _calc_stochastic_slow(df)

        # 최근 봉이 NaN이면 HOLD
        if any(pd.isna(slow_k.iloc[i]) or pd.isna(slow_d.iloc[i]) for i in [-1, -2]):
            return "HOLD"

        # 현재 K가 이미 55 이상이면 반등 많이 진행 → 진입 기회 아님 (40 → 55 완화)
        if slow_k.iloc[-1] >= 55:
            return "HOLD"

        # 최근 cross_window 봉 이내 골든크로스 감지
        # i번째 봉: K > D  /  i-1번째 봉: K <= D  /  크로스 시점 min(K,D) <= 20
        for i in range(-1, -(cross_window + 1), -1):
            cur_k  = slow_k.iloc[i]
            cur_d  = slow_d.iloc[i]
            prev_k = slow_k.iloc[i - 1]
            prev_d = slow_d.iloc[i - 1]
            if any(pd.isna(v) for v in [cur_k, cur_d, prev_k, prev_d]):
                continue
            if cur_k > cur_d and prev_k <= prev_d and min(prev_k, prev_d) <= 20:
                return "BUY"

        # 데드크로스 (과열권)
        lk, ld, pk, pd_ = slow_k.iloc[-1], slow_d.iloc[-1], slow_k.iloc[-2], slow_d.iloc[-2]
        if lk < ld and pk >= pd_ and max(pk, pd_) >= 80:
            return "SELL"

    except Exception as e:
        logger.error(f"Stochastic 오류({code}): {e}")
    return "HOLD"


# ══════════════════════════════════════════
# REVERSION 조건 필터
# ══════════════════════════════════════════

def check_reversion_filters(code: str, basic: dict) -> tuple[bool, list[str]]:
    """
    REVERSION 전략 고급 필터 — 비용 최소화 순서로 재배치.

      [1차] 등락률·가격·거래량  → 로컬 계산 (API 0회) — filter_reversion_candidates에서 처리
      [2차] 거래대금·시총       → 단순 숫자 조회 (API 1회, 가벼움)
      [3차] 5분봉 MA20+스토캐스틱 → 30봉 (API 1회, 중간)
      [4차] 1분봉 125봉 1회 수신 → MA120 + 스토캐스틱 동시 계산 (중복 호출 제거)
    """
    passed = []
    failed = []

    price  = basic.get("price", 0)
    volume = basic.get("volume", 0)

    # ══ 2차: 거래대금 · 시총 · 거래량 (단순 숫자, 가벼운 API) ══════
    detail = get_current_price(code)
    if not detail:
        return False, []

    # ── 전일 거래대금 ─────────────────────────────────────────
    prev_trade_amt = int(basic.get("prev_trade_amount", 0) / 100_000_000)
    if prev_trade_amt >= ADVANCED_FILTER["min_trade_amount"]:
        passed.append(f"전일거래대금({prev_trade_amt:,}억)")
    else:
        logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: 전일거래대금부족({prev_trade_amt:,}억)")
        return False, []

    # ── 거래량 급증 (전일 대비) ───────────────────────────────
    from api.ohlcv import get_prev_ohlcv
    _prev_ohlcv = get_prev_ohlcv(code)
    prev_vol    = (_prev_ohlcv.get("volume", 0) if _prev_ohlcv else 0) or detail.get("prev_volume", 0)
    surge_ratio = volume / prev_vol if prev_vol > 0 else 0
    min_surge   = REVERSION["volume_surge_ratio"]

    if surge_ratio >= min_surge or surge_ratio == 0:
        passed.append(f"거래량급증(전일{surge_ratio:.1f}배)" if surge_ratio > 0 else "거래량급증(확인불가-통과)")
    else:
        logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: 거래량부족(전일{surge_ratio:.1f}배)")
        return False, []

    # ── 시가총액 ──────────────────────────────────────────────
    market_cap = detail.get("market_cap", 0)
    if market_cap > 0:
        if market_cap >= ADVANCED_FILTER["min_market_cap"]:
            passed.append(f"시가총액({market_cap:,}억)")
        else:
            logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: 시가총액미달({market_cap:,}억)")
            return False, []
    else:
        passed.append("시가총액(확인불가-통과)")

    # ══ 3차: 5분봉 MA20 + 스토캐스틱 (30봉, 중간 비용) ══════════
    trend_ok, trend_msg = check_5min_trend(code, price)
    if not trend_ok:
        logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: {trend_msg}")
        return False, []
    passed.append(trend_msg)

    # ══ 4차: 1분봉 125봉 1회 수신 → MA120 + 스토캐스틱 통합 계산 ══
    # 기존: get_minute_chart(100봉) + get_minute_chart_bulk(125봉) = 2회 호출
    # 개선: get_minute_chart_bulk(125봉) 1회로 MA120 + 스토캐스틱 동시 처리
    try:
        candles = get_minute_chart_bulk(code, need=125)
        if len(candles) < 25:
            logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: 1분봉 데이터 부족({len(candles)}개)")
            return False, []

        df = pd.DataFrame(candles).iloc[::-1].reset_index(drop=True)

        # MA120 계산 (상승장 필터)
        if MA120_MARKET_FILTER.get("enabled", True) and len(df) >= 120:
            ma120 = df["close"].rolling(window=120).mean().iloc[-1]
            if not pd.isna(ma120):
                if price < float(ma120):
                    logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: MA120하락({price:,}<{ma120:,.0f})")
                    return False, []
                passed.append(f"MA120상승({price:,}≥{ma120:,.0f})")

        # 스토캐스틱 골든크로스 (동일 데이터 재활용, API 추가 호출 없음)
        slow_k, slow_d = _calc_stochastic_slow(df)

        if any(pd.isna(slow_k.iloc[i]) or pd.isna(slow_d.iloc[i]) for i in [-1, -2]):
            return False, []

        if slow_k.iloc[-1] >= 55:
            logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: K반등과다({slow_k.iloc[-1]:.0f}≥55)")
            return False, []

        cross_window = 3
        buy_found = False
        for i in range(-1, -(cross_window + 1), -1):
            cur_k  = slow_k.iloc[i];  cur_d  = slow_d.iloc[i]
            prev_k = slow_k.iloc[i-1]; prev_d = slow_d.iloc[i-1]
            if any(pd.isna(v) for v in [cur_k, cur_d, prev_k, prev_d]):
                continue
            if cur_k > cur_d and prev_k <= prev_d and min(prev_k, prev_d) <= 20:
                buy_found = True
                passed.append(f"1분봉스토캐스틱(침체탈출,K={cur_k:.0f})")
                break

        if not buy_found:
            logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: 1분봉스토캐스틱(골든크로스없음)")
            return False, []

    except Exception as e:
        logger.error(f"[{code}] 1분봉 통합 필터 오류: {e}")
        return False, []

    return True, passed


def filter_reversion_candidates(stocks: list[dict]) -> list[dict]:
    """REVERSION 조건 필터 적용"""
    positions  = get_positions()
    candidates = []

    from strategy.condition import _bought_codes

    for s in stocks:
        code  = s["code"]
        price = s.get("price", 0)

        if code in _bought_codes or code in positions:
            continue
        if not (CONDITION["min_price"] <= price <= CONDITION["max_price"]):
            continue
        if s.get("volume", 0) < CONDITION["min_volume"]:
            continue

        ok, passed_list = check_reversion_filters(code, s)
        if ok:
            s["passed_filters"] = passed_list
            candidates.append(s)
            logger.info(f"[{s['name']}({code})] ✅ REVERSION 통과: {', '.join(passed_list)}")

        time.sleep(0.3)

    logger.info(f"REVERSION 스캔: {len(stocks)}개 → {len(candidates)}개 후보")
    return candidates


def score_reversion(stock: dict) -> float:
    """
    REVERSION 점수 계산 (양쪽 스토캐스틱 + 5분봉 추세 통과 종목 대상).
      - 전일 거래대금 70%  ← 유동성·안정성 우선
      - 거래량 배율   30%  ← 당일 관심도
    """
    prev_trade_amount = stock.get("prev_trade_amount", 0)   # 원 단위
    volume            = stock.get("volume", 0)
    prev_volume       = stock.get("prev_volume", 1)

    # 전일 거래대금: 3,000억 = 만점 기준
    trade_score  = min(prev_trade_amount / 300_000_000_000, 1.0) * 100
    # 거래량 배율: 5배 = 만점 기준
    surge_ratio  = volume / prev_volume if prev_volume > 0 else 1.0
    volume_score = min(surge_ratio / 5.0, 1.0) * 100

    return round(trade_score * 0.7 + volume_score * 0.3, 2)


# ══════════════════════════════════════════
# 매수 실행
# ══════════════════════════════════════════

def execute_reversion_buy(stock: dict, per_budget: int) -> bool:
    """REVERSION 전략 매수 실행. per_budget: 해당 종목 배분 금액"""
    code  = stock["code"]
    name  = stock["name"]
    price = stock["price"]

    if len(get_positions()) >= MAX_POSITIONS:
        logger.info("최대 보유 종목 수 도달 - 매수 스킵")
        return False

    if not is_buyable_time():
        return False

    if is_daily_loss_halted():
        logger.warning(f"[{name}] ⛔ 일일 손실 한도 초과 — 신규 매수 차단")
        return False

    deposit = get_deposit()
    budget  = min(per_budget, deposit - 10_000)
    if budget <= 0:
        logger.warning(f"예수금 부족 ({deposit:,}원)")
        return False

    qty = calc_buy_qty(price, budget)
    if qty <= 0:
        logger.warning(f"[{name}] 매수 수량 0 - 스킵")
        return False

    logger.info(
        f"[{name}({code})] 🟢 REVERSION 매수 시도 | "
        f"{qty}주 × {price:,}원 | 배분금액: {per_budget:,}원"
    )
    result = buy_market(code, qty)

    if result["success"]:
        add_position(code, name, qty, price, strategy_type=STRATEGY_REVERSION)
        from strategy.condition import _bought_codes, _save_bought_codes
        _bought_codes.add(code)
        _save_bought_codes()
        logger.info(f"[{name}] ✅ REVERSION 매수 완료")
        return True
    else:
        logger.warning(f"[{name}] ❌ REVERSION 매수 실패: {result['msg']}")
        return False


# ══════════════════════════════════════════
# REVERSION 전략 루프
# ══════════════════════════════════════════

def run_reversion(stop_event=None, total_budget: int = 0):
    """
    REVERSION 전략 루프.
    total_budget: main.py에서 전달받은 REVERSION 전략 총 배분 금액.
    """
    SCAN_INTERVAL = REVERSION["scan_interval_sec"]
    MAX_PER_STRAT = MARKET_PHASE.get("max_per_strategy", 3)
    if total_budget <= 0:
        total_budget = ORDER_AMOUNT * MAX_PER_STRAT

    logger.info(
        f"🟢 REVERSION 전략 시작 "
        f"({REVERSION['start_time']} ~ {REVERSION['end_time']}, "
        f"{SCAN_INTERVAL}초 간격 | 총 배분: {total_budget:,}원)"
    )

    watchlist = load_watchlist()
    wl_codes  = {s["code"] for s in watchlist} if watchlist else set()
    if wl_codes:
        logger.info(f"📋 REVERSION watchlist {len(wl_codes)}개 종목 우선 감시")

    while True:
        if stop_event and stop_event.is_set():
            logger.info("REVERSION 전략 종료 (stop_event)")
            break

        if not is_reversion_time():
            # 5초씩 나눠서 대기 → stop_event 즉시 반응 가능
            for _ in range(12):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(5)
            continue

        now_str = datetime.now().strftime("%H:%M:%S")
        positions       = get_positions()
        reversion_count = sum(
            1 for p in positions.values()
            if p.get("strategy_type") == STRATEGY_REVERSION
        )
        logger.info(
            f"🟢 [{now_str}] REVERSION 스캔 | "
            f"보유: {reversion_count}/{MAX_PER_STRAT}개"
        )

        if reversion_count >= MAX_PER_STRAT:
            logger.info(f"⏸ REVERSION 최대 보유 도달({MAX_PER_STRAT}개) → 대기")
            time.sleep(SCAN_INTERVAL)
            continue

        stocks = get_volume_rank(
            top_n=30,
            min_change_rate=REVERSION["min_change_rate"],
            max_change_rate=REVERSION["max_change_rate"],
        )
        if not stocks:
            time.sleep(SCAN_INTERVAL)
            continue

        if wl_codes:
            stocks.sort(key=lambda x: (0 if x["code"] in wl_codes else 1))

        candidates = filter_reversion_candidates(stocks)
        if not candidates:
            logger.info("REVERSION 후보 없음")
            time.sleep(SCAN_INTERVAL)
            continue

        candidates.sort(key=lambda x: score_reversion(x), reverse=True)

        top_candidates  = candidates[:MAX_PER_STRAT]
        scores          = [score_reversion(s) for s in top_candidates]
        remaining_slots = MAX_PER_STRAT - reversion_count
        slot_budget     = int(total_budget / MAX_PER_STRAT)
        # buy_count: 실제 매수할 종목 수 (남은 슬롯 vs 후보 수 중 작은 값)
        # ← slot_budget * remaining_slots 로 하면 후보 1개일 때 3배 예산 배정 버그 발생
        buy_count       = min(remaining_slots, len(top_candidates))
        per_budgets     = calc_position_budgets(
            scores[:buy_count],
            slot_budget * buy_count,
        )

        for stock, per_budget in zip(top_candidates[:buy_count], per_budgets):
            cur_rev_count = sum(
                1 for p in get_positions().values()
                if p.get("strategy_type") == STRATEGY_REVERSION
            )
            if cur_rev_count >= MAX_PER_STRAT:
                break
            logger.info(
                f"[{stock['name']}] 점수: {score_reversion(stock):.1f} | "
                f"배분금액: {per_budget:,}원"
            )
            execute_reversion_buy(stock, per_budget)
            time.sleep(1)

        time.sleep(SCAN_INTERVAL)
