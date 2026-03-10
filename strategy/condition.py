# ============================================================
#  condition.py  –  공유 유틸: 당일 매수 종목 관리 + 디버그
#
#  역할 분리 후 이 파일의 책임:
#    1. _bought_codes 세트 — BREAKOUT / REVERSION 양쪽 공유
#    2. load_bought_codes() / _save_bought_codes() — 파일 영속성
#    3. print_candidates() — 수동 디버그용 후보 출력
#
#  제거된 것 (이전 역할):
#    - run_strategy() 루프       → main.py가 투 트랙 스레드 직접 실행
#    - check_advanced_filters()  → strategy_reversion.py 단독 소유
#    - check_stochastic_signal() → strategy_reversion.py 단독 소유
#    - check_market_phase()      → strategy_reversion.py 단독 소유
#    - execute_buy()             → execute_breakout/reversion_buy로 분리
#    - score_candidate()         → score_breakout/reversion으로 분리
#
#  사용처:
#    strategy_breakout.py  → _bought_codes, _save_bought_codes
#    strategy_reversion.py → _bought_codes, _save_bought_codes
#    main.py               → load_bought_codes (시작 시 1회)
# ============================================================

import os
import json
from datetime import date

from utils.logger import get_logger

logger = get_logger("condition")

# ── 당일 매수 종목 공유 세트 ──────────────────────────────────
# BREAKOUT / REVERSION 양쪽에서 import해서 공유
_bought_codes: set[str] = set()

_BOUGHT_CODES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bought_codes.json"
)


# ══════════════════════════════════════════
# 당일 매수 종목 영속성 관리
# ══════════════════════════════════════════

def _save_bought_codes():
    """
    _bought_codes를 bought_codes.json에 저장.
    날짜를 함께 저장해 다음날 자동 초기화.
    매수 완료 직후 즉시 호출 → 재시작 시 재매수 방지.
    """
    try:
        data = {
            "date" : date.today().isoformat(),
            "codes": list(_bought_codes),
        }
        with open(_BOUGHT_CODES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug(f"당일 매수 종목 저장: {list(_bought_codes)}")
    except Exception as e:
        logger.error(f"bought_codes.json 저장 오류: {e}")


def load_bought_codes():
    """
    프로그램 시작 시 bought_codes.json 불러오기.
    오늘 날짜가 아니면 자동 초기화 (다음날 재매수 허용).
    main.py에서 장 시작 전 1회 호출.

    주의: _bought_codes 를 = set() 으로 재할당하면
    다른 모듈의 'from condition import _bought_codes'가 구 객체를 참조하게 됨.
    반드시 .clear() / .update() 로 동일 객체를 유지해야 함.
    """
    try:
        if not os.path.exists(_BOUGHT_CODES_PATH):
            logger.debug("bought_codes.json 없음 → 빈 세트로 시작")
            _bought_codes.clear()
            return

        with open(_BOUGHT_CODES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("date") != date.today().isoformat():
            logger.info("bought_codes.json 날짜 불일치 → 초기화 (새 거래일)")
            _bought_codes.clear()
            _save_bought_codes()
            return

        _bought_codes.clear()
        _bought_codes.update(data.get("codes", []))
        logger.info(f"당일 매수 종목 복원: {_bought_codes if _bought_codes else '없음'}")

    except Exception as e:
        logger.error(f"bought_codes.json 불러오기 오류: {e}")
        _bought_codes.clear()


# ══════════════════════════════════════════
# 디버그용 후보 출력
# ══════════════════════════════════════════

def print_candidates():
    """
    수동 실행 시 REVERSION 조건 기준으로 현재 후보 종목 출력.
    $ python condition.py 으로 직접 실행 가능.
    """
    try:
        from api.price import get_fluctuation_rank
        from strategy_reversion import filter_reversion_candidates, score_reversion
    except ImportError:
        from api.price import get_fluctuation_rank
        from strategy.strategy_reversion import filter_reversion_candidates, score_reversion

    print("\n[🔍 조건 검색 실행 중...]\n")
    stocks = get_fluctuation_rank(top_n=30)
    if not stocks:
        print("  등락률 순위 조회 실패")
        return

    candidates = filter_reversion_candidates(stocks)
    if not candidates:
        print("  매수 후보 없음")
        return

    candidates.sort(key=lambda x: score_reversion(x), reverse=True)

    print(f"{'='*80}")
    print(f"  🔍 REVERSION 후보 종목 ({len(candidates)}개) — 상위 3개 매수 대상")
    print(f"{'='*80}")
    print(f"  {'점수':>5} {'종목명':<14} {'현재가':>8} {'등락률':>7} {'거래대금':>8}  통과 조건")
    print(f"  {'-'*75}")
    for i, s in enumerate(candidates[:10]):
        score        = score_reversion(s)
        trade_amount = int(s["price"] * s["volume"] / 100_000_000)
        filters      = ", ".join(s.get("passed_filters", []))
        marker       = "★" if i < 3 else " "
        print(
            f"  {marker}{score:>5.1f} {s['name']:<14} {s['price']:>8,} "
            f"{s['change_rate']:>+6.2f}% {trade_amount:>7,}억  {filters}"
        )
    print(f"{'='*80}\n")


if __name__ == "__main__":
    from auth import get_access_token
    get_access_token()
    print_candidates()
