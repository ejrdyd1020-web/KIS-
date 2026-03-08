# ============================================================
#  test_simulation.py  –  BREAKOUT 전략 가상 데이터 시뮬레이션
#
#  목적:
#    실제 API 없이 가상 종목 데이터로 전략 로직 전체 검증
#    - 필터 통과/탈락 사유 확인
#    - RSI(9) 점수 반영 확인
#    - 80점 커트라인 작동 확인
#    - ATR 동적 손절가 계산 확인
#
#  실행:
#    python test_simulation.py
# ============================================================

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# ── config.py 값 직접 내장 (독립 실행 가능) ─────────────────
BREAKOUT = {
    "min_change_rate"   : 3.0,
    "max_change_rate"   : 25.0,
    "volume_surge_ratio": 5.0,
    "stop_loss_pct"     : -3.0,
    "take_profit_pct"   : 5.0,
}
CONDITION = {
    "min_trade_amount"  : 300,
}
SCORE_THRESHOLD = 70.0
MAX_PER_STRAT   = 3

# ════════════════════════════════════════
# 가상 종목 데이터 (09:05 기준, 경과 5분)
# 3월 6일 실제 급등주 패턴 기반으로 구성
# ════════════════════════════════════════

ELAPSED_MIN = 5.0   # 장 시작 후 경과 5분 (09:05 기준)

# vol_rank  : 거래량 순위 (API 반환 순서 기준)
# amt_rank  : 거래대금 순위 (당일 누적거래대금 내림차순)
MOCK_STOCKS = [
    # ── 케이스 1: 거래량/거래대금 상위 + 체결강도/등락률 강함 → 고점수 ──
    {
        "name": "퍼스텍", "code": "004410",
        "price": 9020, "change_rate": 16.8,
        "prev_high": 8900, "prev_trade_amt_억": 450,
        "vol_rank": 5, "amt_rank": 18,
        "exec_strength": 168.0, "atr": 180.0,
        "memo": "✅ 순위 상위 + 체결강도/등락률 강 → 1위 예상",
    },
    # ── 케이스 2: 거래량 2위이나 거래대금 순위 낮음 ─────────
    {
        "name": "서울식품", "code": "011040",
        "price": 217, "change_rate": 11.9,
        "prev_high": 210, "prev_trade_amt_억": 380,
        "vol_rank": 2, "amt_rank": 95,
        "exec_strength": 145.0, "atr": 8.0,
        "memo": "🟡 거래량 2위 but 거래대금 95위 → 점수 확인",
    },
    # ── 케이스 3: 체결강도 낮아 점수 미달 ───────────────────
    {
        "name": "현대ADM", "code": "010820",
        "price": 15540, "change_rate": 13.0,
        "prev_high": 14800, "prev_trade_amt_억": 520,
        "vol_rank": 28, "amt_rank": 22,
        "exec_strength": 112.0, "atr": 320.0,
        "memo": "🟡 체결강도 낮아 점수 미달 예상",
    },
    # ── 케이스 4: 전일 거래대금 부족 필터 탈락 ─────────────
    {
        "name": "소형주A", "code": "123450",
        "price": 3200, "change_rate": 18.5,
        "prev_high": 3100, "prev_trade_amt_억": 85,
        "vol_rank": 8, "amt_rank": 30,
        "exec_strength": 155.0, "atr": 65.0,
        "memo": "❌ 전일 거래대금 85억 < 100억 → 필터 탈락",
    },
    # ── 케이스 5: 전일 고가 미돌파 필터 탈락 ───────────────
    {
        "name": "SK증권", "code": "001510",
        "price": 1931, "change_rate": 10.8,
        "prev_high": 2100, "prev_trade_amt_억": 610,
        "vol_rank": 3, "amt_rank": 27,
        "exec_strength": 132.0, "atr": 42.0,
        "memo": "❌ 현재가 1,931 < 전일고가 2,100 → 필터 탈락",
    },
    # ── 케이스 6: 균형잡힌 2위 매수 신호 ────────────────────
    {
        "name": "성광벤드", "code": "014970",
        "price": 24500, "change_rate": 9.3,
        "prev_high": 24100, "prev_trade_amt_억": 890,
        "vol_rank": 12, "amt_rank": 11,
        "exec_strength": 141.0, "atr": 510.0,
        "memo": "✅ 균형잡힌 순위 + 체결강도 → 2위 예상",
    },
    # ── 케이스 7: 체결강도 부족 필터 탈락 ──────────────────
    {
        "name": "대한전선", "code": "001440",
        "price": 5840, "change_rate": 7.2,
        "prev_high": 5700, "prev_trade_amt_억": 720,
        "vol_rank": 20, "amt_rank": 35,
        "exec_strength": 88.0, "atr": 120.0,
        "memo": "❌ 체결강도 88% < 100% → 필터 탈락",
    },
    # ── 케이스 8: 거래량 순위 낮아 점수 미달 ────────────────
    {
        "name": "한화솔루션", "code": "009830",
        "price": 28100, "change_rate": 5.8,
        "prev_high": 27500, "prev_trade_amt_억": 1200,
        "vol_rank": 180, "amt_rank": 9,
        "exec_strength": 118.0, "atr": 580.0,
        "memo": "🟡 거래량 180위 → 점수 미달 예상",
    },
    # ── 케이스 9: 거래대금 1위 KODEX — 등락률 낮아 점수 확인
    {
        "name": "KODEX레버리지", "code": "122630",
        "price": 18220, "change_rate": 8.9,
        "prev_high": 17900, "prev_trade_amt_억": 980,
        "vol_rank": 4, "amt_rank": 1,
        "exec_strength": 152.0, "atr": 380.0,
        "memo": "✅ 거래대금 1위 + 거래량 4위 → 3위 예상",
    },
    # ── 케이스 10: 등락률+체결강도 최강, 순위도 상위 ────────
    {
        "name": "신규급등주", "code": "999990",
        "price": 12400, "change_rate": 21.5,
        "prev_high": 12000, "prev_trade_amt_억": 320,
        "vol_rank": 9, "amt_rank": 6,
        "exec_strength": 178.0, "atr": 250.0,
        "memo": "✅ 등락률+체결강도 최강 → 1위 경쟁",
    },
]


# ════════════════════════════════════════
# 점수 계산 (실제 score_breakout 로직 동일)
# ════════════════════════════════════════

def calc_score(s: dict, elapsed_min: float = None) -> float:
    """score_breakout() 동일 로직 — API 없이 가상 데이터로 계산

    가중치:
      체결강도 30% (200% = 만점)
      등락률   30% (25%  = 만점)
      거래량   20% (1위=20점, 300위=0점 | 공식: 20 - 20/300*(순위-1))
      거래대금 20% (1위=20점, 300위=0점 | 공식: 20 - 20/300*(순위-1))
    """
    exec_strength = s["exec_strength"]
    change_rate   = s["change_rate"]
    vol_rank      = s.get("vol_rank", 300)
    amt_rank      = s.get("amt_rank", 300)

    # 거래량 순위 점수 (1위=20점 만점)
    vol_score  = max(20 - (20 / 300 * (vol_rank - 1)), 0)

    # 거래대금 순위 점수 (1위=20점 만점)
    amt_score  = max(20 - (20 / 300 * (amt_rank - 1)), 0)

    # 체결강도 (200% = 만점 → 100점 환산 후 30% 가중)
    strength_score = min(exec_strength / 200.0, 1.0) * 100

    # 등락률 (25% = 만점 → 100점 환산 후 30% 가중)
    rate_score = min(change_rate / 25.0, 1.0) * 100

    return round(
        strength_score * 0.3 +
        rate_score     * 0.3 +
        vol_score      +        # 이미 20점 기준
        amt_score,              # 이미 20점 기준
        2
    )


# ════════════════════════════════════════
# 필터 체크 (실제 check_breakout_filters 로직 동일)
# ════════════════════════════════════════

def check_filters(s: dict, elapsed_min: float) -> tuple[bool, list[str], list[str]]:
    passed = []
    failed = []

    # 0. 종목명 기반 제외
    name = s.get("name", "")
    EXCLUDE_KEYWORDS = [
        "ETF", "ETN",
        "KODEX", "TIGER", "KBSTAR", "KOSEF",
        "ARIRANG", "HANARO", "SOL", "ACE",
        "리츠", "REIT", "스팩", "SPAC",
    ]
    excluded = any(kw in name for kw in EXCLUDE_KEYWORDS)
    if not excluded and (name.endswith("우") or name.endswith("우B") or name.endswith("우C")):
        excluded = True
    if excluded:
        failed.append(f"제외종목({name})")
        return False, passed, failed

    # 1. 등락률
    cr = s["change_rate"]
    if BREAKOUT["min_change_rate"] <= cr <= BREAKOUT["max_change_rate"]:
        passed.append(f"등락률({cr:+.1f}%)")
    else:
        failed.append(f"등락률범위외({cr:+.1f}%)")

    # 2. 전일 거래대금 100억+
    ta = s["prev_trade_amt_억"]
    if ta >= CONDITION.get("min_trade_amount", 100):
        passed.append(f"전일거래대금({ta:,}억)")
    else:
        failed.append(f"전일거래대금부족({ta:,}억)")

    # 3. 거래량 순위 300위 이내
    vol_rank = s.get("vol_rank", 999)
    if vol_rank <= 300:
        passed.append(f"거래량순위({vol_rank}위/300위이내)")
    else:
        failed.append(f"거래량순위초과({vol_rank}위>300위)")
    # ※ 거래대금 순위는 필터 제거 → 점수(20%)에만 반영

    # 4. 전일 고가 돌파
    ph = s["prev_high"]
    if ph > 0:
        if s["price"] > ph:
            passed.append(f"전일고가돌파({s['price']:,}>{ph:,})")
        else:
            failed.append(f"전일고가미돌파({s['price']:,}<={ph:,})")
    else:
        passed.append("전일고가(데이터없음-통과)")

    # 5. 체결강도 100%+
    es = s["exec_strength"]
    if es >= 100.0:
        passed.append(f"체결강도({es:.1f}%)")
    elif es == 0:
        passed.append("체결강도(확인불가-통과)")
    else:
        failed.append(f"체결강도부족({es:.1f}%)")

    return len(failed) == 0, passed, failed


# ════════════════════════════════════════
# 시뮬레이션 실행
# ════════════════════════════════════════

def run_simulation():
    print(f"\n{'='*72}")
    print(f"  🔵 BREAKOUT 전략 가상 데이터 시뮬레이션")
    print(f"  시뮬레이션 시각: 09:05 (장 시작 후 {ELAPSED_MIN:.0f}분 경과)")
    print(f"  커트라인: {SCORE_THRESHOLD:.0f}점  |  최대 매수: {MAX_PER_STRAT}종목")
    print(f"{'='*72}\n")

    results    = []
    buy_list   = []

    for s in MOCK_STOCKS:
        ok, f_passed, f_failed = check_filters(s, ELAPSED_MIN)
        score = calc_score(s, ELAPSED_MIN)

        # ATR 동적 손절가
        atr        = s.get("atr", 0)
        price      = s["price"]
        fixed_stop = int(price * (1 + BREAKOUT["stop_loss_pct"] / 100))
        atr_stop   = int(price - atr * 1.5) if atr > 0 else 0
        hard_stop  = max(atr_stop, fixed_stop) if atr > 0 else fixed_stop
        take_profit = int(price * (1 + BREAKOUT["take_profit_pct"] / 100))

        buy_signal = ok and score >= SCORE_THRESHOLD

        result = {
            **s,
            "filter_ok"  : ok,
            "f_passed"   : f_passed,
            "f_failed"   : f_failed,
            "score"      : score,
            "hard_stop"  : hard_stop,
            "take_profit": take_profit,
            "buy_signal" : buy_signal,
        }
        results.append(result)
        if buy_signal:
            buy_list.append(result)

    # ── 전체 현황 테이블 ─────────────────────────────────────
    print(f"  {'종목명':<14} {'현재가':>8} {'등락률':>7} {'거래량순위':>9} "
          f"{'거래대금순위':>10} {'체결강도':>8} {'점수':>7} {'판정'}")
    print(f"  {'-'*74}")

    for r in results:
        if r["buy_signal"]:
            판정 = "🟢 매수신호"
        elif not r["filter_ok"]:
            판정 = "🔴 필터탈락"
        else:
            판정 = "🟡 점수미달"

        print(
            f"  {r['name']:<14} {r['price']:>8,} "
            f"{r['change_rate']:>+6.1f}% "
            f"{r['vol_rank']:>8}위 "
            f"{r['amt_rank']:>9}위 "
            f"{r['exec_strength']:>7.0f}% "
            f"{r['score']:>6.1f}점  "
            f"{판정}"
        )

    # ── 매수 신호 종목 상세 ──────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  🟢 매수 신호 종목 ({len(buy_list)}개) — 상위 {MAX_PER_STRAT}개만 실제 매수 대상")
    print(f"{'='*72}")

    buy_list.sort(key=lambda x: x["score"], reverse=True)

    for i, r in enumerate(buy_list[:MAX_PER_STRAT], 1):
        stop_type = f"ATR({r['atr']:,.0f}×1.5)" if r["atr"] > 0 else "고정(-3%)"
        stop_pct  = (r["hard_stop"] / r["price"] - 1) * 100
        take_pct  = (r["take_profit"] / r["price"] - 1) * 100
        print(f"""
  [{i}위] {r['name']} ({r['code']})  ← {r['memo']}
    현재가    : {r['price']:,}원  |  등락률: {r['change_rate']:+.1f}%
    거래량순위: {r['vol_rank']}위  |  거래대금순위: {r['amt_rank']}위  |  체결강도: {r['exec_strength']:.0f}%
    전일거래대금: {r['prev_trade_amt_억']:,}억
    ─────────────────────────────────────────
    총점      : {r['score']:.1f}점 / 100점  (커트라인 {SCORE_THRESHOLD:.0f}점)
    손절가    : {r['hard_stop']:,}원 ({stop_type}, {stop_pct:+.1f}%)
    익절가    : {r['take_profit']:,}원 ({take_pct:+.1f}%)
    통과필터  : {chr(10) + '                ' if len(r['f_passed']) > 3 else ''}{', '.join(r['f_passed'])}""")

    if len(buy_list) > MAX_PER_STRAT:
        print(f"\n  ※ {len(buy_list) - MAX_PER_STRAT}개 추가 신호 있으나 슬롯 초과로 미매수")

    # ── 탈락 종목 사유 요약 ──────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  🔴 탈락/미달 종목 사유 ({len(results) - len(buy_list)}개)")
    print(f"{'='*72}")
    for r in results:
        if r["buy_signal"]:
            continue
        if r["filter_ok"]:
            reason = f"점수미달({r['score']:.1f}점 < {SCORE_THRESHOLD:.0f}점)"
        else:
            reason = " | ".join(r["f_failed"])
        print(f"  ▸ {r['name']:<14} {reason}")
        print(f"    {r['memo']}")

    # ── 점수 구성 요소 분석 ──────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  📊 점수 구성 요소 상세 분석")
    print(f"{'='*72}")
    print(f"  {'종목명':<14} {'체결강도(30%)':>13} {'등락률(30%)':>11} {'거래량순위(20%)':>14} {'거래대금순위(20%)':>16} {'합계':>7}")
    print(f"  {'-'*76}")
    for r in results:
        str_s  = min(r['exec_strength'] / 200.0, 1.0) * 100
        rate_s = min(r['change_rate'] / 25.0, 1.0) * 100
        vol_s  = max(20 - (20 / 300 * (r['vol_rank'] - 1)), 0)
        amt_s  = max(20 - (20 / 300 * (r['amt_rank'] - 1)), 0)

        print(
            f"  {r['name']:<14} "
            f"{str_s * 0.3:>10.1f}점  "
            f"{rate_s * 0.3:>8.1f}점  "
            f"{vol_s:>11.1f}점({r['vol_rank']}위)  "
            f"{amt_s:>11.1f}점({r['amt_rank']}위)  "
            f"{r['score']:>6.1f}점"
        )

    print(f"\n{'='*72}")
    print(f"  💡 시뮬레이션 결과 요약")
    print(f"{'='*72}")
    print(f"  전체 {len(results)}개 종목 분석")
    print(f"  매수 신호: {len(buy_list)}개  |  실제 매수 대상: {min(len(buy_list), MAX_PER_STRAT)}개")
    탈락 = sum(1 for r in results if not r["filter_ok"])
    미달 = sum(1 for r in results if r["filter_ok"] and not r["buy_signal"])
    print(f"  필터 탈락: {탈락}개  |  점수 미달: {미달}개")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    run_simulation()
