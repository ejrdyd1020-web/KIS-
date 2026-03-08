# ============================================================
#  strategy/strategy_reversion.py  –  전략 B: REVERSION
#
#  운용 시간: 09:10 ~ 15:20
#
#  핵심 타점: 스토캐스틱 침체권 골든크로스 + 이평 정배열
#
#  매수 조건:
#    1. 등락률 +3% ~ +15% (과열 구간 회피)
#    2. 전일 거래대금 300억 이상
#    3. 거래량 급증 (전일 2배 OR 1분봉 500%)
#    4. 시가총액 100억 이상
#    5. 체결강도 100% 이상
#    6. 스토캐스틱 슬로우 침체권 골든크로스 (K<20 → K>D)
#    7. 1분봉 MA120 상승장 (현재가 >= MA120)
#
#  손절 / 익절:
#    - 고정 손절  : -2.5% (지지선 이탈 기준 — A보다 타이트)
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
from api.chart             import get_volume_ratio_1min, get_minute_chart, get_minute_chart_bulk
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
        logger.warning(f"[{code}] MA120 확인 불가 → 상승장으로 간주")
        return True
    is_bull = price >= ma120
    logger.debug(f"[{code}] 현재가={price:,} / MA120={ma120:,.1f} → {'상승장' if is_bull else '하락장'}")
    return is_bull


# ══════════════════════════════════════════
# 스토캐스틱 슬로우
# ══════════════════════════════════════════

def _calc_stochastic_slow(df, k_period=12, d_period=5, smooth_period=5):
    low_min  = df["low"].rolling(window=k_period).min()
    high_max = df["high"].rolling(window=k_period).max()
    denom    = (high_max - low_min).replace(0, float("nan"))
    fast_k   = ((df["close"] - low_min) / denom) * 100
    slow_k   = fast_k.rolling(window=smooth_period).mean()
    slow_d   = slow_k.rolling(window=d_period).mean()
    return slow_k, slow_d


def check_stochastic_signal(code: str) -> str:
    """
    스토캐스틱 슬로우 매수/매도/관망 신호.
      BUY  : 침체권(20↓) 골든크로스
      SELL : 과열권(80↑) 데드크로스
      HOLD : 그 외
    """
    try:
        candles = get_minute_chart(code, count=100)
        if len(candles) < 25:
            return "HOLD"
        df = pd.DataFrame(candles).iloc[::-1].reset_index(drop=True)
        slow_k, slow_d = _calc_stochastic_slow(df)
        lk, ld, pk, pd_ = slow_k.iloc[-1], slow_d.iloc[-1], slow_k.iloc[-2], slow_d.iloc[-2]
        if any(pd.isna(v) for v in [lk, ld, pk, pd_]):
            return "HOLD"
        if lk > ld and pk <= pd_ and min(pk, pd_) <= 20:
            return "BUY"
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
    REVERSION 전략 고급 필터.

      1. 등락률 +3% ~ +15%
      2. 전일 거래대금 300억 이상
      3. 거래량 급증 (전일 2배 OR 1분봉 500%)
      4. 시가총액 100억 이상
      5. 체결강도 100% 이상
      6. 스토캐스틱 침체권 골든크로스
      7. MA120 상승장
    """
    passed = []
    failed = []

    detail = get_current_price(code)
    if not detail:
        return False, []

    price       = basic.get("price", 0)
    volume      = basic.get("volume", 0)
    change_rate = basic.get("change_rate", 0)

    # ── 1. 등락률 범위 (REVERSION은 +15% 상한) ───────────────
    min_r = REVERSION["min_change_rate"]
    max_r = REVERSION["max_change_rate"]
    if min_r <= change_rate <= max_r:
        passed.append(f"등락률({change_rate:+.1f}%)")
    else:
        failed.append(f"등락률범위외({change_rate:+.1f}%)")

    # ── 2. 전일 거래대금 ──────────────────────────────────────
    prev_trade_amt = int(basic.get("prev_trade_amount", 0) / 100_000_000)
    if prev_trade_amt >= ADVANCED_FILTER["min_trade_amount"]:
        passed.append(f"전일거래대금({prev_trade_amt:,}억)")
    else:
        failed.append(f"전일거래대금부족({prev_trade_amt:,}억)")

    # ── 3. 거래량 급증 (OR 조건) ──────────────────────────────
    vol_ratio_1min = get_volume_ratio_1min(code)
    prev_vol       = detail.get("prev_volume", 0)
    surge_ratio    = volume / prev_vol if prev_vol > 0 else 0
    min_surge      = REVERSION["volume_surge_ratio"]

    vol_1min_ok  = vol_ratio_1min >= ADVANCED_FILTER["min_volume_ratio_1min"]
    vol_daily_ok = surge_ratio    >= min_surge

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

    # ── 4. 시가총액 ───────────────────────────────────────────
    market_cap = detail.get("market_cap", 0)
    if market_cap > 0:
        if market_cap >= ADVANCED_FILTER["min_market_cap"]:
            passed.append(f"시가총액({market_cap:,}억)")
        else:
            failed.append(f"시가총액미달({market_cap:,}억)")
    else:
        passed.append("시가총액(확인불가-통과)")

    # ── 5. 체결강도 ───────────────────────────────────────────
    exec_strength = detail.get("exec_strength", 0.0)
    if exec_strength >= ADVANCED_FILTER["min_execution_strength"]:
        passed.append(f"체결강도({exec_strength:.1f}%)")
    elif exec_strength == 0:
        passed.append("체결강도(확인불가-통과)")
    else:
        failed.append(f"체결강도부족({exec_strength:.1f}%)")

    # ── 6. 스토캐스틱 골든크로스 ─────────────────────────────
    stoch = check_stochastic_signal(code)
    if stoch == "BUY":
        passed.append("스토캐스틱(침체탈출)")
    else:
        failed.append(f"스토캐스틱(관망/{stoch})")

    # ── 7. MA120 상승장 ───────────────────────────────────────
    if check_market_phase(code, price):
        passed.append("MA120(상승장)")
    else:
        failed.append("MA120(하락장-진입불가)")

    all_passed = len(failed) == 0
    if failed:
        logger.debug(f"[{basic.get('name', code)}] REVERSION 탈락: {', '.join(failed)}")

    return all_passed, passed


def filter_reversion_candidates(stocks: list[dict]) -> list[dict]:
    """REVERSION 조건 필터 적용"""
    positions  = get_positions()
    candidates = []

    try:
        from condition import _bought_codes
    except ImportError:
        _bought_codes = set()

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
    REVERSION 점수 계산.
      - 거래량 (전일 대비 비율 기준) 40%
      - 등락률  30%
      - 체결강도 30%
    """
    change_rate   = stock.get("change_rate", 0)
    volume        = stock.get("volume", 0)
    prev_vol      = stock.get("prev_volume", 0)
    exec_strength = stock.get("exec_strength", 100.0)

    # 전일 대비 비율 기준 (소형주/대형주 형평성)
    surge_ratio   = volume / prev_vol if prev_vol > 0 else 1.0
    volume_score  = min(surge_ratio / 5.0, 1.0) * 100    # 5배 = 만점
    rate_score    = min(change_rate / 15.0, 1.0) * 100   # 15% = 만점 (REVERSION 상한)
    strength_score = min(exec_strength / 200.0, 1.0) * 100

    return round(volume_score * 0.4 + rate_score * 0.3 + strength_score * 0.3, 2)


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
        try:
            import condition as _cond
            _cond._bought_codes.add(code)
            _cond._save_bought_codes()
        except Exception:
            pass
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
            time.sleep(60)
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

        stocks = get_volume_rank(top_n=30)
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
        per_budgets     = calc_position_budgets(
            scores[:remaining_slots],
            slot_budget * remaining_slots,
        )

        for stock, per_budget in zip(top_candidates[:remaining_slots], per_budgets):
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
