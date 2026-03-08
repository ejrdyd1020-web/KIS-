# ============================================================
#  test_breakout_log.py  –  BREAKOUT 거래량 기준 모의 로그 테스트
#
#  목적:
#    실제 매수 없이 BREAKOUT 조건 통과 종목 수 확인
#    거래량 기준(5배/3배/2배)별 통과율 비교
#
#  실행:
#    python test_breakout_log.py
# ============================================================

import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from auth            import get_access_token
from api.price       import get_volume_rank, get_current_price
from api.ohlcv       import get_prev_ohlcv
from config          import BREAKOUT, CONDITION

get_access_token()

# ── 테스트할 거래량 급증 기준 ────────────────────────────────
SURGE_THRESHOLDS = [5.0, 3.0, 2.0]   # 배수 기준 비교


def run_test(top_n: int = 30):
    print(f"\n{'='*70}")
    print(f"  🔵 BREAKOUT 모의 로그 테스트")
    print(f"  등락률 범위: +{BREAKOUT['min_change_rate']}% ~ +{BREAKOUT['max_change_rate']}%")
    print(f"  거래량 기준 비교: {SURGE_THRESHOLDS} 배")
    print(f"{'='*70}\n")

    # 거래량 순위 조회
    stocks = get_volume_rank(top_n=top_n)
    if not stocks:
        print("  ❌ 거래량 순위 조회 실패")
        return

    print(f"  📋 거래량 순위 상위 {len(stocks)}개 종목 스캔 중...\n")

    results = []

    for s in stocks:
        code        = s["code"]
        name        = s["name"]
        price       = s["price"]
        change_rate = s["change_rate"]
        volume      = s["volume"]

        # 전일 OHLCV 캐시 조회
        prev = get_prev_ohlcv(code)
        prev_vol       = prev["volume"]        if prev else s.get("prev_volume", 0)
        prev_high      = prev["high"]          if prev else 0
        prev_trade_amt = int(prev["trade_amount"] / 100_000_000) if prev else 0

        # 거래량 급증 비율 (분당 환산)
        from datetime import datetime
        now          = datetime.now()
        market_open  = now.replace(hour=9, minute=0, second=0, microsecond=0)
        elapsed_min  = max((now - market_open).seconds / 60, 1)
        if prev_vol > 0:
            surge_ratio = (volume / elapsed_min) / (prev_vol / 390.0)
        else:
            surge_ratio = 0

        # 현재가 상세 조회 (체결강도)
        detail        = get_current_price(code)
        exec_strength = detail.get("exec_strength", 0) if detail else 0

        # 전일 고가 돌파 여부
        high_break = price > prev_high if prev_high > 0 else None

        results.append({
            "code"          : code,
            "name"          : name,
            "price"         : price,
            "change_rate"   : change_rate,
            "surge_ratio"   : surge_ratio,
            "prev_vol"      : prev_vol,
            "volume"        : volume,
            "prev_high"     : prev_high,
            "high_break"    : high_break,
            "exec_strength" : exec_strength,
            "prev_trade_amt": prev_trade_amt,
        })

    # ── 결과 출력 ────────────────────────────────────────────
    print(f"  {'종목명':<14} {'현재가':>8} {'등락률':>7} {'거래량배수':>9} "
          f"{'고가돌파':>7} {'체결강도':>8} {'전일거래대금':>10}")
    print(f"  {'-'*68}")

    for r in results:
        high_str = "✅돌파" if r["high_break"] else ("❌미달" if r["high_break"] is False else "확인불가")
        print(
            f"  {r['name']:<14} {r['price']:>8,} "
            f"{r['change_rate']:>+6.1f}% "
            f"{r['surge_ratio']:>8.1f}배 "
            f"{high_str:>8} "
            f"{r['exec_strength']:>7.0f}% "
            f"{r['prev_trade_amt']:>9,}억"
        )

    # ── 거래량 기준별 통과 수 비교 ───────────────────────────
    print(f"\n{'='*70}")
    print(f"  📊 거래량 기준별 BREAKOUT 통과 수 비교 (전일거래대금 300억+ 기준)")
    print(f"{'='*70}")

    for threshold in SURGE_THRESHOLDS:
        passed = [
            r for r in results
            if r["surge_ratio"] >= threshold
            and r["prev_trade_amt"] >= 300
            and r["exec_strength"] >= 100
        ]
        print(f"\n  📌 거래량 {threshold:.0f}배 기준 → {len(passed)}개 통과")
        if passed:
            for r in passed:
                high_str = "✅고가돌파" if r["high_break"] else ("❌고가미달" if r["high_break"] is False else "고가확인불가")
                print(
                    f"     ▸ {r['name']:<12} "
                    f"등락률 {r['change_rate']:+.1f}% | "
                    f"거래량 {r['surge_ratio']:.1f}배 | "
                    f"{high_str}"
                )
        else:
            print(f"     → 통과 종목 없음")

    print(f"\n{'='*70}")
    print(f"  💡 권장: 5배 기준 통과 0~1개면 3배로 낮추는 것 고려")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    run_test(top_n=30)
