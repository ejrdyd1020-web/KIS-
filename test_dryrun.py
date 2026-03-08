# ============================================================
#  test_dryrun.py  –  BREAKOUT 전략 드라이런 (매수 없이 실전 시뮬레이션)
#
#  목적:
#    실제 장 데이터로 BREAKOUT 전략 전체 로직을 실행하되
#    매수 주문만 실행하지 않고 결과를 로그로 출력.
#
#    확인 가능한 것:
#      - 조건 통과/탈락 사유
#      - RSI(9) 점수
#      - 거래량 급증 배수 (분당 환산)
#      - 최종 점수 및 80점 커트라인 통과 여부
#      - "실제로 샀다면" 매수가 / 손절가 / 익절가
#
#  실행 시간:
#    09:00 ~ 09:10 사이에 실행해야 의미있는 데이터 확인 가능
#    (그 외 시간에도 실행 가능하나 거래량 급증 종목이 적음)
#
#  실행:
#    python test_dryrun.py
#    python test_dryrun.py --loop   ← 5초 간격 반복 (09:10까지)
# ============================================================

import os
import sys
import time
import argparse
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from auth                        import get_access_token
from api.price                   import get_volume_rank, get_current_price
from api.ohlcv                   import get_prev_ohlcv, get_atr
from strategy.strategy_breakout  import (
    check_breakout_filters,
    score_breakout,
    filter_breakout_candidates,
)
from strategy.strategy_reversion import calc_rsi_1min
from config                      import BREAKOUT, CONDITION, MARKET_PHASE

get_access_token()

SCORE_THRESHOLD = 80.0
MAX_PER_STRAT   = MARKET_PHASE.get("max_per_strategy", 3)

# ── 드라이런 결과 누적 (루프 모드에서 중복 제거) ─────────────
_seen_codes: set = set()


def run_dryrun(top_n: int = 30, loop_mode: bool = False):
    now_str = datetime.now().strftime("%H:%M:%S")

    print(f"\n{'='*72}")
    print(f"  🔵 BREAKOUT 드라이런  [{now_str}]")
    print(f"  등락률: +{BREAKOUT['min_change_rate']}% ~ +{BREAKOUT['max_change_rate']}%  |  "
          f"커트라인: {SCORE_THRESHOLD:.0f}점  |  최대 {MAX_PER_STRAT}종목")
    print(f"{'='*72}\n")

    # ── 거래량 순위 조회 ─────────────────────────────────────
    stocks = get_volume_rank(top_n=top_n)
    if not stocks:
        print("  ❌ 거래량 순위 조회 실패")
        return

    print(f"  📋 거래량 순위 상위 {len(stocks)}개 종목 분석 중...\n")

    results   = []
    passed_list = []

    for s in stocks:
        code        = s["code"]
        name        = s["name"]
        price       = s["price"]
        change_rate = s["change_rate"]
        volume      = s["volume"]

        # ── 전일 OHLCV ───────────────────────────────────────
        prev           = get_prev_ohlcv(code)
        prev_vol       = prev["volume"]                        if prev else 0
        prev_high      = prev["high"]                         if prev else 0
        prev_trade_amt = int(prev["trade_amount"] / 1e8)      if prev else 0

        # ── 분당 환산 거래량 배수 ─────────────────────────────
        now          = datetime.now()
        market_open  = now.replace(hour=9, minute=0, second=0, microsecond=0)
        elapsed_min  = max((now - market_open).seconds / 60, 1)
        surge_ratio  = (volume / elapsed_min) / (prev_vol / 390.0) if prev_vol > 0 else 0

        # ── 현재가 상세 (체결강도) ────────────────────────────
        detail        = get_current_price(code)
        exec_strength = detail.get("exec_strength", 0) if detail else 0

        # ── RSI(9) ────────────────────────────────────────────
        rsi_val = calc_rsi_1min(code, period=9)

        # ── ATR (동적 손절가) ─────────────────────────────────
        atr = get_atr(code)

        # ── 전체 필터 체크 ────────────────────────────────────
        ok, filter_passed = check_breakout_filters(code, s)

        # ── 점수 계산 ─────────────────────────────────────────
        s["exec_strength"] = exec_strength
        total_score = score_breakout(s)

        # ── 가상 손절가 / 익절가 계산 ────────────────────────
        stop_loss_pct   = BREAKOUT["stop_loss_pct"]
        take_profit_pct = BREAKOUT["take_profit_pct"]
        fixed_stop      = int(price * (1 + stop_loss_pct / 100))
        atr_stop        = int(price - atr * 1.5) if atr > 0 else 0
        hard_stop       = max(atr_stop, fixed_stop) if atr > 0 else fixed_stop
        take_profit     = int(price * (1 + take_profit_pct / 100))

        result = {
            "code"          : code,
            "name"          : name,
            "price"         : price,
            "change_rate"   : change_rate,
            "surge_ratio"   : surge_ratio,
            "exec_strength" : exec_strength,
            "rsi_val"       : rsi_val,
            "prev_high"     : prev_high,
            "high_break"    : price > prev_high if prev_high > 0 else None,
            "prev_trade_amt": prev_trade_amt,
            "atr"           : atr,
            "hard_stop"     : hard_stop,
            "take_profit"   : take_profit,
            "filter_ok"     : ok,
            "filter_passed" : filter_passed,
            "score"         : total_score,
            "buy_signal"    : ok and total_score >= SCORE_THRESHOLD,
        }
        results.append(result)
        if result["buy_signal"]:
            passed_list.append(result)

        time.sleep(0.3)

    # ════════════════════════════════════════
    # 전체 종목 상세 현황
    # ════════════════════════════════════════
    print(f"  {'종목명':<14} {'현재가':>8} {'등락률':>7} {'거래량배수':>9} "
          f"{'RSI':>6} {'체결강도':>7} {'점수':>6} {'판정':>8}")
    print(f"  {'-'*72}")

    for r in results:
        high_str  = "✅돌파" if r["high_break"] else ("❌미달" if r["high_break"] is False else "-")
        buy_str   = "🟢매수" if r["buy_signal"] else ("🔴탈락" if not r["filter_ok"] else "🟡점수미달")
        rsi_str   = f"{r['rsi_val']:.0f}" if r["rsi_val"] != 50.0 else "N/A"
        print(
            f"  {r['name']:<14} {r['price']:>8,} "
            f"{r['change_rate']:>+6.1f}% "
            f"{r['surge_ratio']:>8.1f}배 "
            f"{rsi_str:>6} "
            f"{r['exec_strength']:>6.0f}% "
            f"{r['score']:>5.1f}점 "
            f"{buy_str:>8}"
        )

    # ════════════════════════════════════════
    # 매수 신호 종목 상세
    # ════════════════════════════════════════
    print(f"\n{'='*72}")
    print(f"  🟢 매수 신호 종목 ({len(passed_list)}개) — 실제 매수는 실행되지 않음")
    print(f"{'='*72}")

    if passed_list:
        # 점수 높은 순 정렬
        passed_list.sort(key=lambda x: x["score"], reverse=True)
        for i, r in enumerate(passed_list[:MAX_PER_STRAT], 1):
            stop_type = f"ATR({r['atr']:,.0f}×1.5)" if r["atr"] > 0 else "고정(-3%)"
            print(f"""
  [{i}위] {r['name']} ({r['code']})
    현재가   : {r['price']:,}원  |  등락률: {r['change_rate']:+.1f}%
    거래량   : {r['surge_ratio']:.1f}배 (분당환산)  |  체결강도: {r['exec_strength']:.0f}%
    RSI(9)   : {r['rsi_val']:.1f}  |  전일거래대금: {r['prev_trade_amt']:,}억
    총점     : {r['score']:.1f}점 / 100점
    손절가   : {r['hard_stop']:,}원 ({stop_type})
    익절가   : {r['take_profit']:,}원 (+{BREAKOUT['take_profit_pct']}%)
    통과필터 : {", ".join(r['filter_passed'])}""")
    else:
        print(f"  → 매수 신호 없음 (필터 탈락 또는 점수 미달)")

    # ════════════════════════════════════════
    # 탈락 종목 사유 요약
    # ════════════════════════════════════════
    failed_list = [r for r in results if not r["buy_signal"]]
    if failed_list:
        print(f"\n{'='*72}")
        print(f"  🔴 탈락 종목 사유 요약 ({len(failed_list)}개)")
        print(f"{'='*72}")
        for r in failed_list:
            if r["filter_ok"] and r["score"] < SCORE_THRESHOLD:
                reason = f"점수미달({r['score']:.1f}점)"
            else:
                reason = "필터탈락"
            print(f"  ▸ {r['name']:<14} {reason}")

    print(f"\n{'='*72}\n")
    return passed_list


# ════════════════════════════════════════
# 실행 진입점
# ════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="5초 간격 반복 (09:10까지)")
    parser.add_argument("--top", type=int, default=30, help="거래량 순위 상위 N개 (기본 30)")
    args = parser.parse_args()

    if args.loop:
        print("\n🔄 루프 모드 시작 — 09:10까지 5초 간격으로 스캔합니다 (Ctrl+C로 중단)")
        scan_count = 0
        try:
            while True:
                now = datetime.now().strftime("%H:%M")
                if now >= BREAKOUT["end_time"]:
                    print(f"\n⏰ {BREAKOUT['end_time']} — BREAKOUT 구간 종료")
                    break
                scan_count += 1
                print(f"\n[스캔 #{scan_count}]")
                run_dryrun(top_n=args.top, loop_mode=True)
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n👋 드라이런 중단 (Ctrl+C)")
    else:
        run_dryrun(top_n=args.top)
