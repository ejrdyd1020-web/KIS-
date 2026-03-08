# ============================================================
#  strategy/strategy_breakout.py  –  전략 A: BREAKOUT
#
#  운용 시간: 09:00 ~ 09:10 (장 초반 10분)
#
#  핵심 타점: 전일 고가 돌파 + 거래량 급증 → 주도주 선점
#
#  매수 조건:
#    1. 등락률 +3% ~ +25%
#    2. 거래량 전일 대비 5배 이상
#    3. 전일 고가 돌파 (현재가 > 전일 고가)
#    4. 체결강도 100% 이상
#    5. 전일 거래대금 300억 이상
#    ※ 스토캐스틱 / MA 필터 OFF (장초반 캔들 부족)
#
#  손절 / 익절:
#    - 고정 손절  : -3.0%
#    - 고정 익절  : +5.0%
#    - 트레일링   : 수익 3% 이상 시 작동, 고점 대비 -2.0%
#
#  스캔 주기: 5초 (실시간)
# ============================================================

import time
import sys
import os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from premarket             import load_watchlist
from api.price             import get_current_price, get_volume_rank
from api.order             import buy_market, calc_buy_qty
from api.balance           import get_deposit
from strategy.position     import (
    add_position, get_positions,
    is_daily_loss_halted, is_buyable_time,
)
from utils.logger          import get_logger
from config import (
    MAX_POSITIONS, ORDER_AMOUNT,
    BREAKOUT, CONDITION, MARKET_PHASE,
    STRATEGY_BREAKOUT,
)

try:
    from api.index import calc_position_budgets
except ImportError:
    from index import calc_position_budgets

logger = get_logger("breakout")


# ══════════════════════════════════════════
# 시간 체크
# ══════════════════════════════════════════

def is_breakout_time() -> bool:
    """09:00 ~ 09:10 구간 여부"""
    now = datetime.now().strftime("%H:%M")
    return BREAKOUT["start_time"] <= now < BREAKOUT["end_time"]


# ══════════════════════════════════════════
# BREAKOUT 조건 필터
# ══════════════════════════════════════════

def check_breakout_filters(code: str, basic: dict) -> tuple[bool, list[str]]:
    """
    BREAKOUT 전략 매수 조건 체크.

    체크 항목:
      1. 등락률 범위
      2. 전일 거래대금 300억 이상
      3. 거래량 급증 (전일 5배 이상)
      4. 전일 고가 돌파
      5. 체결강도 100% 이상
    """
    passed = []
    failed = []

    detail = get_current_price(code)
    if not detail:
        return False, []

    price       = basic.get("price", 0)
    change_rate = basic.get("change_rate", 0)
    volume      = basic.get("volume", 0)

    # ── 1. 등락률 범위 ─────────────────────────────────────────
    min_r = BREAKOUT["min_change_rate"]
    max_r = BREAKOUT["max_change_rate"]
    if min_r <= change_rate <= max_r:
        passed.append(f"등락률({change_rate:+.1f}%)")
    else:
        failed.append(f"등락률범위외({change_rate:+.1f}%)")

    # ── 2. 전일 거래대금 300억 이상 ───────────────────────────
    prev_trade_amt = int(basic.get("prev_trade_amount", 0) / 100_000_000)
    if prev_trade_amt >= CONDITION.get("min_trade_amount", 300):
        passed.append(f"전일거래대금({prev_trade_amt:,}억)")
    else:
        failed.append(f"전일거래대금부족({prev_trade_amt:,}억)")

    # ── 3. 거래량 급증 (전일 5배) ─────────────────────────────
    prev_vol    = detail.get("prev_volume", 0)
    surge_ratio = volume / prev_vol if prev_vol > 0 else 0
    min_surge   = BREAKOUT["volume_surge_ratio"]

    if surge_ratio >= min_surge:
        passed.append(f"거래량급증({surge_ratio:.1f}배)")
    elif surge_ratio == 0:
        passed.append("거래량급증(확인불가-통과)")
    else:
        failed.append(f"거래량부족({surge_ratio:.1f}배/{min_surge}배기준)")

    # ── 4. 전일 고가 돌파 ─────────────────────────────────────
    week52_high = detail.get("week52_high", 0)
    prev_high   = basic.get("prev_high", 0)

    # prev_high 없으면 week52_high로 대체 불가 → 현재가 > 시가 조건으로 fallback
    if prev_high > 0:
        if price > prev_high:
            passed.append(f"전일고가돌파({price:,}>{prev_high:,})")
        else:
            failed.append(f"전일고가미돌파({price:,}<={prev_high:,})")
    else:
        # 전일 고가 데이터 없을 때 — 시가 대비 +1% 이상이면 상승 모멘텀 인정
        open_price = detail.get("open", 0)
        if open_price > 0 and price >= open_price * 1.01:
            passed.append(f"시가대비상승({price:,}>{open_price:,})")
        else:
            passed.append("전일고가(데이터없음-통과)")

    # ── 5. 체결강도 100% 이상 ────────────────────────────────
    exec_strength = detail.get("exec_strength", 0.0)
    if exec_strength >= 100.0:
        passed.append(f"체결강도({exec_strength:.1f}%)")
    elif exec_strength == 0:
        passed.append("체결강도(확인불가-통과)")
    else:
        failed.append(f"체결강도부족({exec_strength:.1f}%)")

    all_passed = len(failed) == 0
    if failed:
        logger.debug(f"[{basic.get('name', code)}] BREAKOUT 탈락: {', '.join(failed)}")

    return all_passed, passed


def filter_breakout_candidates(stocks: list[dict]) -> list[dict]:
    """BREAKOUT 조건 필터 적용"""
    positions  = get_positions()
    candidates = []

    # import는 함수 내 지연 import로 순환참조 방지
    try:
        from condition import _bought_codes
    except ImportError:
        _bought_codes = set()

    for s in stocks:
        code  = s["code"]
        price = s.get("price", 0)

        # 이미 보유 중이거나 당일 매수 이력 있으면 스킵
        if code in _bought_codes or code in positions:
            continue

        # 가격 범위 기본 필터
        if not (CONDITION["min_price"] <= price <= CONDITION["max_price"]):
            continue

        ok, passed_list = check_breakout_filters(code, s)
        if ok:
            s["passed_filters"] = passed_list
            candidates.append(s)
            logger.info(
                f"[{s['name']}({code})] ✅ BREAKOUT 통과: {', '.join(passed_list)}"
            )

        time.sleep(0.2)   # API 호출 간격

    logger.info(f"BREAKOUT 스캔: {len(stocks)}개 → {len(candidates)}개 후보")
    return candidates


def score_breakout(stock: dict) -> float:
    """
    BREAKOUT 종목 점수 계산.
      - 거래량 급증도 50% (모멘텀 핵심)
      - 등락률        30%
      - 체결강도      20%
    """
    change_rate   = stock.get("change_rate", 0)
    volume        = stock.get("volume", 0)
    prev_vol      = stock.get("prev_volume", 0)
    exec_strength = stock.get("exec_strength", 100.0)

    surge_ratio   = volume / prev_vol if prev_vol > 0 else 1.0

    surge_score   = min(surge_ratio / 10.0, 1.0) * 100   # 10배 = 만점
    rate_score    = min(change_rate / 25.0, 1.0) * 100
    strength_score = min(exec_strength / 200.0, 1.0) * 100

    return round(surge_score * 0.5 + rate_score * 0.3 + strength_score * 0.2, 2)


# ══════════════════════════════════════════
# 매수 실행
# ══════════════════════════════════════════

def execute_breakout_buy(stock: dict, per_budget: int) -> bool:
    """BREAKOUT 전략 매수 실행. per_budget: 해당 종목 배분 금액"""
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
        f"[{name}({code})] 🔵 BREAKOUT 매수 시도 | "
        f"{qty}주 × {price:,}원 | 배분금액: {per_budget:,}원"
    )
    result = buy_market(code, qty)

    if result["success"]:
        add_position(code, name, qty, price, strategy_type=STRATEGY_BREAKOUT)
        try:
            import condition as _cond
            _cond._bought_codes.add(code)
            _cond._save_bought_codes()
        except Exception:
            pass
        logger.info(f"[{name}] ✅ BREAKOUT 매수 완료")
        return True
    else:
        logger.warning(f"[{name}] ❌ BREAKOUT 매수 실패: {result['msg']}")
        return False


# ══════════════════════════════════════════
# BREAKOUT 전략 루프
# ══════════════════════════════════════════

def run_breakout(stop_event=None, total_budget: int = 0):
    """
    BREAKOUT 전략 루프.
    total_budget: main.py에서 전달받은 BREAKOUT 전략 총 배분 금액.
    """
    SCAN_INTERVAL  = BREAKOUT["scan_interval_sec"]
    MAX_PER_STRAT  = MARKET_PHASE.get("max_per_strategy", 3)
    # total_budget 미전달 시 fallback
    if total_budget <= 0:
        total_budget = ORDER_AMOUNT * MAX_PER_STRAT

    logger.info(
        f"🔵 BREAKOUT 전략 시작 "
        f"({BREAKOUT['start_time']} ~ {BREAKOUT['end_time']}, "
        f"{SCAN_INTERVAL}초 간격 | 총 배분: {total_budget:,}원)"
    )

    watchlist = load_watchlist()
    wl_codes  = {s["code"] for s in watchlist} if watchlist else set()
    if wl_codes:
        logger.info(f"📋 BREAKOUT watchlist {len(wl_codes)}개 종목 우선 감시")

    while True:
        if stop_event and stop_event.is_set():
            logger.info("BREAKOUT 전략 종료 (stop_event)")
            break

        if not is_breakout_time():
            logger.info("⏰ BREAKOUT 구간 종료 (09:10) → REVERSION 전환")
            break

        now_str     = datetime.now().strftime("%H:%M:%S")
        # BREAKOUT 전략 전용 보유 수 계산
        positions      = get_positions()
        breakout_count = sum(
            1 for p in positions.values()
            if p.get("strategy_type") == STRATEGY_BREAKOUT
        )
        logger.info(
            f"🔵 [{now_str}] BREAKOUT 스캔 | "
            f"보유: {breakout_count}/{MAX_PER_STRAT}개"
        )

        if breakout_count >= MAX_PER_STRAT:
            time.sleep(SCAN_INTERVAL)
            continue

        stocks = get_volume_rank(top_n=30)
        if not stocks:
            time.sleep(SCAN_INTERVAL)
            continue

        if wl_codes:
            stocks.sort(key=lambda x: (0 if x["code"] in wl_codes else 1))

        candidates = filter_breakout_candidates(stocks)
        if not candidates:
            logger.info("BREAKOUT 후보 없음")
            time.sleep(SCAN_INTERVAL)
            continue

        candidates.sort(key=lambda x: score_breakout(x), reverse=True)

        # 상위 MAX_PER_STRAT개만 대상
        top_candidates = candidates[:MAX_PER_STRAT]
        scores         = [score_breakout(s) for s in top_candidates]

        # 점수 비율 자금 배분
        remaining_slots = MAX_PER_STRAT - breakout_count
        slot_budget     = int(total_budget / MAX_PER_STRAT)   # 슬롯당 기준 금액
        per_budgets     = calc_position_budgets(
            scores[:remaining_slots],
            slot_budget * remaining_slots,
        )

        for stock, per_budget in zip(top_candidates[:remaining_slots], per_budgets):
            if sum(
                1 for p in get_positions().values()
                if p.get("strategy_type") == STRATEGY_BREAKOUT
            ) >= MAX_PER_STRAT:
                break
            logger.info(
                f"[{stock['name']}] 점수: {score_breakout(stock):.1f} | "
                f"배분금액: {per_budget:,}원"
            )
            execute_breakout_buy(stock, per_budget)
            time.sleep(0.5)

        time.sleep(SCAN_INTERVAL)
