# ============================================================
#  strategy/condition.py  –  고급 조건 검색 + 자동 매수 전략
#
#  필터 조건 (HTS 조건식 기준으로 수정):
#    1. 등락률 7~25%          ✅ 변경 (기존 2~10%)
#    2. 1분봉 거래량 500% 급증 (직전 1분봉 대비)
#    3. 52주 신고가 근접 (최고가 98% 이상)
#    4. 시가총액 100억 이상    ✅ 변경 (기존 2,500억)
#    5. 거래대금 300억 이상
#    6. 당일 25% 이상 상승 종목 제외  ✅ 변경 (기존 20%)
#    7. 매수호가 잔량 55% 이상
#    8. 체결강도 100% 이상     ✅ 추가 (HTS 조건식 F)
# ============================================================

import time
import sys
import os
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from api.price    import get_fluctuation_rank, get_current_price, get_asking_price
from api.chart    import get_volume_ratio_1min
from api.order    import buy_market, calc_buy_qty
from api.balance  import get_deposit
from monitor.position import add_position, get_positions
from utils.logger import get_logger
from config import (
    ORDER_AMOUNT,
    MAX_POSITIONS,
    CONDITION,
    ADVANCED_FILTER,
    CONDITION_SCAN_END,
    MARKET_OPEN,
)

logger = get_logger("strategy")

_bought_codes: set[str] = set()


def is_scan_time() -> bool:
    now = datetime.now().strftime("%H:%M")
    return MARKET_OPEN <= now <= CONDITION_SCAN_END


def check_advanced_filters(code: str, basic: dict) -> tuple[bool, list[str]]:
    """고급 필터 체크"""
    passed = []
    failed = []

    detail = get_current_price(code)
    if not detail:
        return False, []

    price       = basic.get("price", 0)
    volume      = basic.get("volume", 0)
    change_rate = basic.get("change_rate", 0)

    # ── 1. 당일 과열 제외 ────────────────────────────────────
    if change_rate >= ADVANCED_FILTER["max_day_change_rate"]:
        failed.append(f"과열({change_rate:.1f}%)")
    else:
        passed.append(f"등락률정상({change_rate:.1f}%)")

    # ── 2. 전일 거래대금 300억 이상 ──────────────────────────
    prev_trade_amount = int(basic.get("prev_trade_amount", 0) / 100_000_000)
    if prev_trade_amount >= ADVANCED_FILTER["min_trade_amount"]:
        passed.append(f"전일거래대금({prev_trade_amount:,}억)")
    else:
        failed.append(f"전일거래대금부족({prev_trade_amount:,}억)")

    # ── 3. 거래량 급증 (OR 조건) ─────────────────────────────
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
        if vol_daily_ok:
            passed.append(f"거래량급증(전일{surge_ratio:.1f}배)")
        else:
            passed.append("거래량급증(확인불가-통과)")
    else:
        failed.append(f"거래량부족(전일{surge_ratio:.1f}배/1분봉{vol_ratio_1min:.0f}%)")

    # ── 4. 52주 신고가 근접 ───────────────────────────────────
    week52_high = detail.get("week52_high", 0)
    if week52_high > 0:
        high_ratio = price / week52_high * 100
        if high_ratio >= ADVANCED_FILTER["week52_high_pct"]:
            passed.append(f"52주신고가({high_ratio:.1f}%)")
        else:
            failed.append(f"52주신고가미달({high_ratio:.1f}%)")
    else:
        passed.append("52주신고가(확인불가-통과)")

    # ── 5. 시가총액 100억 이상 ────────────────────────────────
    market_cap = detail.get("market_cap", 0)
    if market_cap > 0:
        if market_cap >= ADVANCED_FILTER["min_market_cap"]:
            passed.append(f"시가총액({market_cap:,}억)")
        else:
            failed.append(f"시가총액미달({market_cap:,}억)")
    else:
        passed.append("시가총액(확인불가-통과)")

    # ── 6. 매수/매도 호가 잔량 비율 ──────────────────────────
    asking = get_asking_price(code)
    if asking:
        bid_ratio = asking.get("bid_ratio", 50.0)
        if bid_ratio >= ADVANCED_FILTER["bid_ask_ratio"]:
            passed.append(f"매수우세({bid_ratio:.1f}%)")
        else:
            failed.append(f"매수세약({bid_ratio:.1f}%)")
    else:
        passed.append("호가비율(확인불가-통과)")

    # ── 7. 체결강도 100% 이상 ✅ 추가 ────────────────────────
    execution_strength = detail.get("exec_strength", 0.0)  # price.py 키: exec_strength
    if execution_strength > 0:
        if execution_strength >= ADVANCED_FILTER["min_execution_strength"]:
            passed.append(f"체결강도({execution_strength:.1f}%)")
        else:
            failed.append(f"체결강도부족({execution_strength:.1f}%)")
    else:
        passed.append("체결강도(확인불가-통과)")

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
    """등락률 + 거래량 복합 점수 (각 50%)"""
    rate   = stock.get("change_rate", 0)
    volume = stock.get("volume", 0)
    return round(min(rate / 25.0, 1.0) * 50 + min(volume / 1_000_000, 1.0) * 50, 2)


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
    logger.info("🔍 조건 검색 전략 시작")

    while True:
        if stop_event and stop_event.is_set():
            logger.info("조건 검색 전략 종료")
            break

        if not is_scan_time():
            time.sleep(60)
            continue

        if len(get_positions()) >= MAX_POSITIONS:
            time.sleep(SCAN_INTERVAL)
            continue

        stocks = get_fluctuation_rank(top_n=30)
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

    print(f"{'='*75}")
    print(f"  🔍 매수 후보 종목 ({len(candidates)}개)")
    print(f"{'='*75}")
    print(f"  {'점수':>5} {'종목명':<14} {'현재가':>8} {'등락률':>7} {'거래대금':>8}  통과 조건")
    print(f"  {'-'*70}")
    for s in candidates[:10]:
        score        = score_candidate(s)
        trade_amount = int(s['price'] * s['volume'] / 100_000_000)
        filters      = ", ".join(s.get("passed_filters", []))
        print(f"  {score:>5.1f} {s['name']:<14} {s['price']:>8,} "
              f"{s['change_rate']:>+6.2f}% {trade_amount:>7,}억  {filters}")
    print(f"{'='*75}\n")


if __name__ == "__main__":
    from auth import get_access_token
    get_access_token()
    print_candidates()
