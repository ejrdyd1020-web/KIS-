# ============================================================
#  strategy/strategy_breakout.py  –  전략 A: BREAKOUT
#
#  운용 시간: 09:00 ~ 09:10 (장 초반 10분)
#
#  핵심 타점: 전일 고가 돌파 + 거래량 급증 → 주도주 선점
#
#  매수 조건:
#    1. 등락률 +3% ~ +25%
#    2. 거래량 전일 대비 5배 이상 (분당 환산)
#    3. 전일 고가 돌파 (현재가 > 전일 고가)
#    4. 체결강도 100% 이상
#    5. 전일 거래대금 300억 이상
#    ※ RSI / 스토캐스틱 / MA 필터 OFF (장초반 캔들 부족, 거래량+체결강도로 판단)
#
#  점수 산정 (100점 만점, 80점 이상만 매수):
#    - 체결강도      30% (120% = 만점)
#    - 등락률        30% (15% = 만점)
#    - 거래량 급증   20% (분당 환산, 20배 = 만점)
#    - 거래대금 급증 20% (분당 환산, 20배 = 만점)
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
from api.ohlcv             import get_prev_ohlcv, get_prev_high, get_prev_trade_amount, get_prev_volume, get_atr
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

def check_breakout_filters(code: str, basic: dict) -> tuple[bool, list[str], list[str]]:
    """
    BREAKOUT 전략 매수 조건 체크.

    체크 항목:
      1. 등락률 범위
      2. 전일 거래대금 100억 이상
      3. 거래량 급증 (전일 5배 이상)
      4. 전일 고가 돌파
      5. 체결강도 100% 이상
    """
    passed = []
    failed = []

    detail = get_current_price(code)
    if not detail:
        return False, [], ["현재가조회실패"]

    price       = basic.get("price", 0)
    change_rate = basic.get("change_rate", 0)
    volume      = basic.get("volume", 0)

    # ── 0. 종목명 기반 제외 필터 ────────────────────────────────
    #  API fid_trgt_exls_cls_code로 1차 제외하지만
    #  ETF, 리츠, SPAC 등이 빠져나오는 경우 대비 2차 필터
    name = basic.get("name", "")
    EXCLUDE_KEYWORDS = [
        "ETF", "ETN",                          # ETF/ETN
        "KODEX", "TIGER", "KBSTAR", "KOSEF",   # ETF 브랜드
        "ARIRANG", "HANARO", "SOL", "ACE",
        "리츠", "REIT",                         # 리츠
        "스팩", "SPAC",                         # 스팩
        # 우선주는 endswith로 별도 체크 (우리기술 등 오탐 방지)
    ]
    excluded = any(kw in name for kw in EXCLUDE_KEYWORDS)
    # 우선주: 종목명 끝이 '우', '우B', '우C' 패턴
    if not excluded and (name.endswith("우") or name.endswith("우B") or name.endswith("우C")):
        excluded = True

    if excluded:
        failed.append(f"제외종목({name})")
        return False, passed, failed

    # ── 1. 등락률 범위 ─────────────────────────────────────────
    min_r = BREAKOUT["min_change_rate"]
    max_r = BREAKOUT["max_change_rate"]
    if min_r <= change_rate <= max_r:
        passed.append(f"등락률({change_rate:+.1f}%)")
    else:
        failed.append(f"등락률범위외({change_rate:+.1f}%)")

    # ── 2. 전일 거래대금 유동성 확인 (분당 환산 비교) ───────
    #
    #  장 초반엔 당일 누적거래대금이 작으므로 절대값 비교 불가.
    #  거래량과 동일하게 분당 평균으로 환산해서 비교.
    #
    #  전일 거래대금 분당 평균 = 전일 총거래대금 ÷ 390분
    #  오늘 거래대금 분당 평균 = 오늘 누적거래대금 ÷ 경과분
    #  amt_surge = 오늘 분당 평균 ÷ 전일 분당 평균
    #
    prev_ohlcv        = get_prev_ohlcv(code)
    prev_trade_raw    = (
        prev_ohlcv["trade_amount"] if prev_ohlcv
        else basic.get("prev_trade_amount", 0)
    )
    today_trade_raw   = basic.get("trade_amount", 0)   # 당일 누적거래대금(원)

    now_for_amt       = datetime.now()
    market_open_amt   = now_for_amt.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_min_amt   = max((now_for_amt - market_open_amt).seconds / 60, 1)

    if prev_trade_raw > 0 and today_trade_raw > 0:
        prev_amt_per_min  = prev_trade_raw / 390.0
        today_amt_per_min = today_trade_raw / elapsed_min_amt
        amt_surge         = today_amt_per_min / prev_amt_per_min

        min_amt_surge = BREAKOUT.get("trade_amount_surge_ratio", 2.0)  # 기본 2배
        if amt_surge >= min_amt_surge:
            passed.append(f"거래대금급증({amt_surge:.1f}배/분당환산)")
        else:
            failed.append(f"거래대금부족({amt_surge:.1f}배/{min_amt_surge}배기준)")
    else:
        # 데이터 없으면 통과 (ohlcv 캐시 미수집 시 매매 막히지 않도록)
        passed.append("거래대금(확인불가-통과)")

    # ── 3. 거래량 급증 (분당 환산 비교) ─────────────────────────
    #
    #  전일 총거래량 기준 직접 비교 시 장 초반(09:00~09:10)에
    #  하루치 거래량을 넘기는 건 불가능 → 분당 평균으로 환산 비교.
    #
    #  전일 분당 평균 = 전일 총거래량 ÷ 390분 (6.5시간)
    #  오늘 분당 평균 = 오늘 누적거래량 ÷ 장 시작 후 경과분
    #  surge_ratio   = 오늘 분당 평균 ÷ 전일 분당 평균
    #
    prev_vol  = (
        prev_ohlcv["volume"] if prev_ohlcv
        else detail.get("prev_volume", 0)
    )
    min_surge = BREAKOUT["volume_surge_ratio"]

    if prev_vol > 0:
        now           = datetime.now()
        market_open   = now.replace(hour=9, minute=0, second=0, microsecond=0)
        elapsed_min   = max((now - market_open).seconds / 60, 1)  # 최소 1분
        prev_per_min  = prev_vol / 390.0          # 전일 분당 평균
        today_per_min = volume / elapsed_min      # 오늘 분당 평균
        surge_ratio   = today_per_min / prev_per_min if prev_per_min > 0 else 0

        if surge_ratio >= min_surge:
            passed.append(f"거래량급증({surge_ratio:.1f}배/분당환산)")
        else:
            failed.append(f"거래량부족({surge_ratio:.1f}배/{min_surge}배기준/분당환산)")
    else:
        surge_ratio = 0
        passed.append("거래량급증(확인불가-통과)")

    # ── 4. 전일 고가 돌파 (ohlcv 캐시 우선) ──────────────────
    prev_high = (
        prev_ohlcv["high"] if prev_ohlcv
        else basic.get("prev_high", 0)
    )

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

    return all_passed, passed, failed


def filter_breakout_candidates(stocks: list[dict]) -> list[dict]:
    """BREAKOUT 조건 필터 적용"""
    positions  = get_positions()
    candidates = []

    # import는 함수 내 지연 import로 순환참조 방지
    from strategy.condition import _bought_codes

    for s in stocks:
        code  = s["code"]
        price = s.get("price", 0)

        # 이미 보유 중이거나 당일 매수 이력 있으면 스킵
        if code in _bought_codes or code in positions:
            continue

        # 가격 범위 기본 필터
        if not (CONDITION["min_price"] <= price <= CONDITION["max_price"]):
            continue

        ok, passed_list, failed_list = check_breakout_filters(code, s)
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
    BREAKOUT 종목 점수 계산 (100점 만점).

    가중치:
      - 체결강도      30% (120% = 만점)
      - 등락률        30% (15% = 만점)
      - 거래량 순위   20% (1위 = 만점, 300위 = 0점)
      - 거래대금 순위 20% (1위 = 만점, 300위 = 0점)

    순위 점수 공식: 20 - (20/300 × 순위)
    매수 집행 커트라인: 80점 이상
    """
    change_rate     = stock.get("change_rate", 0)
    vol_rank        = stock.get("volume_rank", stock.get("vol_rank", 300))   # 거래량 순위
    amt_rank        = stock.get("trade_rank",  stock.get("amt_rank",  300))  # 거래대금 순위

    # 체결강도: volume-rank API에 없으므로 get_current_price로 별도 조회
    from api.price import get_current_price as _gcp
    _detail       = _gcp(stock.get("code", ""))
    exec_strength = _detail.get("exec_strength", 100.0) if _detail else 100.0

    # ── 거래량 순위 점수 (1위=20점, 300위≈0점) ───────────────
    # 공식: 20 - (20/300 × (순위-1))  →  1위=20점, 300위=0.07점
    vol_score  = max(20 - (20 / 300 * (vol_rank - 1)), 0)

    # ── 거래대금 순위 점수 (1위=20점, 300위≈0점) ─────────────
    amt_score  = max(20 - (20 / 300 * (amt_rank - 1)), 0)

    # ── 체결강도 점수 (200% = 만점 30점) ─────────────────────
    exec_max      = BREAKOUT.get("exec_strength_max", 120.0)
    strength_score = min(exec_strength / exec_max, 1.0) * 100

    # ── 등락률 점수 (15% = 만점 30점) ────────────────────────
    rate_max   = BREAKOUT.get("change_rate_max", 15.0)
    rate_score = min(change_rate / rate_max, 1.0) * 100

    total = round(
        strength_score * 0.3 +
        rate_score     * 0.3 +
        vol_score      +        # 이미 20점 기준
        amt_score,              # 이미 20점 기준
        2
    )
    return total


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
        # ATR 조회 → 동적 손절가 계산용
        atr = get_atr(code)
        add_position(code, name, qty, price, strategy_type=STRATEGY_BREAKOUT)
        from strategy.condition import _bought_codes, _save_bought_codes
        _bought_codes.add(code)
        _save_bought_codes()
        logger.info(f"[{name}] ✅ BREAKOUT 매수 완료 | ATR: {atr:,.0f}원")
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

        stocks = get_volume_rank(top_n=300)
        if not stocks:
            time.sleep(SCAN_INTERVAL)
            continue

        # ── 거래량 순위 부여 (API 반환 순서 = 거래량 순위) ───
        for i, s in enumerate(stocks, 1):
            s["vol_rank"] = i

        # ── 거래대금 순위 부여 (당일 누적거래대금 내림차순) ──
        sorted_by_amt = sorted(stocks, key=lambda x: x.get("trade_amount", 0), reverse=True)
        amt_rank_map  = {s["code"]: i for i, s in enumerate(sorted_by_amt, 1)}
        for s in stocks:
            s["amt_rank"] = amt_rank_map.get(s["code"], 300)

        if wl_codes:
            stocks.sort(key=lambda x: (0 if x["code"] in wl_codes else 1))

        candidates = filter_breakout_candidates(stocks)
        if not candidates:
            logger.info("BREAKOUT 후보 없음")
            time.sleep(SCAN_INTERVAL)
            continue

        # score_breakout 내부에서 get_current_price API를 호출하므로
        # 정렬·자금배분·커트라인 체크 모두 여기서 1회씩만 호출
        scored_candidates = [(score_breakout(c), c) for c in candidates]
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        top_scored     = scored_candidates[:MAX_PER_STRAT]
        top_candidates = [c for _, c in top_scored]
        scores         = [s for s, _ in top_scored]

        # 점수 비율 자금 배분
        remaining_slots = MAX_PER_STRAT - breakout_count
        slot_budget     = int(total_budget / MAX_PER_STRAT)   # 슬롯당 기준 금액
        per_budgets     = calc_position_budgets(
            scores[:remaining_slots],
            slot_budget * remaining_slots,
        )

        SCORE_THRESHOLD = 70.0   # 매수 집행 최소 점수

        for (s, stock), per_budget in zip(top_scored[:remaining_slots], per_budgets):
            if sum(
                1 for p in get_positions().values()
                if p.get("strategy_type") == STRATEGY_BREAKOUT
            ) >= MAX_PER_STRAT:
                break

            logger.info(
                f"[{stock['name']}] 점수: {s:.1f}점 | "
                f"배분금액: {per_budget:,}원"
            )

            # ── 70점 커트라인 ───────────────────────────────
            if s < SCORE_THRESHOLD:
                logger.info(
                    f"[{stock['name']}] ⛔ 점수 미달 ({s:.1f} < {SCORE_THRESHOLD}) → 매수 스킵"
                )
                continue

            execute_breakout_buy(stock, per_budget)
            time.sleep(0.5)

        time.sleep(SCAN_INTERVAL)
