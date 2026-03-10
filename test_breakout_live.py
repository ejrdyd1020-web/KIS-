# ============================================================
#  test_breakout_live.py  —  실제 API로 BREAKOUT 전략 점검
#
#  실행 방법:
#    cd C:\Users\홍윤석\AppData\Roaming\Claude\kis_autotrader
#    python test_breakout_live.py
#
#  장 시작 전/후 언제든 실행 가능
#  → 실제 거래량 순위 조회 + 필터 + 점수 계산까지 전체 확인
# ============================================================

import os, sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 1. 토큰 발급 ──────────────────────────────────────────────
print("=" * 60)
print("  🔑 토큰 발급 중...")
print("=" * 60)
try:
    from auth import get_access_token
    get_access_token()
    print("  ✅ 토큰 발급 성공\n")
except Exception as e:
    print(f"  ❌ 토큰 발급 실패: {e}")
    sys.exit(1)

# ── 1-1. ohlcv 캐시 선행 로드 ─────────────────────────────────
try:
    from api.ohlcv import load_ohlcv_cache
    load_ohlcv_cache()
except Exception as e:
    print(f"  ⚠ ohlcv 캐시 로드 실패: {e}")

# ── 2. 거래량 순위 조회 ───────────────────────────────────────
print("=" * 60)
print("  📊 거래량 순위 상위 20개 조회 중...")
print("=" * 60)
try:
    from api.price import get_volume_rank
    stocks = get_volume_rank(top_n=20)
    print(f"  ✅ {len(stocks)}개 종목 조회 완료\n")
except Exception as e:
    print(f"  ❌ 거래량 순위 조회 실패: {e}")
    sys.exit(1)

if not stocks:
    print("  ⚠ 조회된 종목 없음 (장 시작 전이거나 API 오류)")
    sys.exit(0)

# ── 3. vol_rank / amt_rank 부여 ───────────────────────────────
for i, s in enumerate(stocks, 1):
    s["vol_rank"] = i

sorted_by_amt = sorted(stocks, key=lambda x: x.get("trade_amount", 0), reverse=True)
amt_rank_map  = {s["code"]: i for i, s in enumerate(sorted_by_amt, 1)}
for s in stocks:
    s["amt_rank"] = amt_rank_map.get(s["code"], 20)

# ── 4. 원시 데이터 출력 ───────────────────────────────────────
now = datetime.now()
market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
elapsed_min = max((now - market_open).total_seconds() / 60, 1)

print(f"  현재 시각: {now.strftime('%H:%M:%S')}  |  경과분: {elapsed_min:.1f}분\n")
print(f"  {'종목명':<14} {'현재가':>8} {'등락률':>7} {'거래량순위':>9} {'거래대금순위':>10} {'당일거래대금':>12} {'전일거래대금':>12}")
print(f"  {'-'*76}")

for s in stocks[:20]:
    today_amt_억 = s.get('trade_amount', 0) / 100_000_000
    # 전일거래대금은 ohlcv 캐시 우선, 없으면 API값
    try:
        from api.ohlcv import get_prev_ohlcv
        prev_ohlcv = get_prev_ohlcv(s['code'])
        prev_amt_억 = prev_ohlcv['trade_amount'] / 100_000_000 if prev_ohlcv else s.get('prev_trade_amount', 0) / 100_000_000
    except Exception:
        prev_amt_억 = s.get('prev_trade_amount', 0) / 100_000_000
    print(
        f"  {s['name']:<14} {s['price']:>8,} "
        f"{s.get('change_rate', 0):>+6.1f}% "
        f"{s['vol_rank']:>8}위 "
        f"{s['amt_rank']:>9}위 "
        f"{today_amt_억:>10.1f}억 "
        f"{prev_amt_억:>10.1f}억"
    )

# ── 5. 거래대금 분당환산 비율 계산 ───────────────────────────
print(f"\n{'=' * 60}")
print(f"  📈 거래대금 분당환산 비율 (기준: 2배 이상)")
print(f"{'=' * 60}")
print(f"  {'종목명':<14} {'전일분당평균':>12} {'오늘분당평균':>12} {'급증비율':>9} {'판정'}")
print(f"  {'-'*60}")

for s in stocks[:20]:
    today_raw = s.get('trade_amount', 0)
    # 전일거래대금은 ohlcv 캐시 우선
    try:
        from api.ohlcv import get_prev_ohlcv
        prev_ohlcv = get_prev_ohlcv(s['code'])
        prev_raw = prev_ohlcv['trade_amount'] if prev_ohlcv else s.get('prev_trade_amount', 0)
    except Exception:
        prev_raw = s.get('prev_trade_amount', 0)

    if prev_raw > 0 and today_raw > 0:
        prev_per_min  = prev_raw / 390.0
        today_per_min = today_raw / elapsed_min
        ratio = today_per_min / prev_per_min
        판정 = "✅ 통과" if ratio >= 2.0 else "❌ 미달"
    else:
        ratio = 0
        판정 = "⚠ 데이터없음"

    print(
        f"  {s['name']:<14} "
        f"{prev_raw/390/100_000_000:>10.1f}억/분  "
        f"{today_raw/elapsed_min/100_000_000:>10.1f}억/분  "
        f"{ratio:>7.1f}배  "
        f"{판정}"
    )

# ── 6. BREAKOUT 필터 + 점수 전체 실행 ────────────────────────
print(f"\n{'=' * 60}")
print(f"  🔵 BREAKOUT 필터 + 점수 전체 실행")
print(f"{'=' * 60}")

try:
    from api.ohlcv import _cache as _ohlcv_cache
    print(f"  ohlcv 캐시: {len(_ohlcv_cache)}개 종목\n")
except Exception as e:
    print(f"  ohlcv 캐시 확인 실패: {e}\n")

try:
    from strategy.strategy_breakout import check_breakout_filters, score_breakout

    results = []
    for s in stocks:
        try:
            result = check_breakout_filters(s["code"], s)
            if len(result) == 3:
                ok, passed, failed = result
            else:
                ok, passed = result
                failed = []
            score = score_breakout(s) if ok else 0
            results.append({**s, "ok": ok, "passed": passed, "failed": failed, "score": score})
        except Exception as e:
            results.append({**s, "ok": False, "passed": [], "failed": [str(e)], "score": 0})

    # 결과 출력
    buy_list = [r for r in results if r["ok"] and r["score"] >= 70]
    buy_list.sort(key=lambda x: x["score"], reverse=True)

    print(f"  {'종목명':<14} {'등락률':>7} {'점수':>7} {'판정':<12} 탈락사유")
    print(f"  {'-'*72}")
    for r in results:
        if r["ok"] and r["score"] >= 70:
            판정 = "🟢 매수신호"
            사유 = ""
        elif not r["ok"]:
            판정 = "🔴 필터탈락"
            사유 = " | ".join(r["failed"][:2])
        else:
            판정 = "🟡 점수미달"
            사유 = f"{r['score']:.1f}점"

        print(f"  {r['name']:<14} {r.get('change_rate',0):>+6.1f}%  {r['score']:>6.1f}점  {판정:<12} {사유}")

    print(f"\n  🟢 매수 신호: {len(buy_list)}개  |  커트라인: 70점")
    if buy_list:
        print(f"\n  상위 매수 후보:")
        for i, r in enumerate(buy_list[:3], 1):
            print(f"    [{i}위] {r['name']}  {r['score']:.1f}점  통과: {', '.join(r['passed'][:3])}")

except ImportError as e:
    print(f"  ❌ strategy 모듈 import 실패: {e}")
    print(f"     → strategy_breakout.py 경로 확인 필요")
except Exception as e:
    print(f"  ❌ 필터/점수 실행 오류: {e}")
    import traceback
    traceback.print_exc()

print(f"\n{'=' * 60}")
print(f"  점검 완료  {datetime.now().strftime('%H:%M:%S')}")
print(f"{'=' * 60}\n")
