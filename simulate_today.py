"""
오늘 09:00 BREAKOUT 시뮬레이션
- 분봉 수집 없이 ohlcv 캐시 + price API 데이터만 사용
- 전일 분당 평균 = 전일거래량 / 390
- 오늘 분당 평균 = 당일누적거래량 / 경과분
실행: python simulate_today.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from auth import get_access_token
get_access_token()

from api.ohlcv import load_ohlcv_cache, get_prev_ohlcv
load_ohlcv_cache()

from api.price import get_volume_rank, get_current_price
from auth import get_headers, get_base_url
from config import BREAKOUT, ADVANCED_FILTER
from datetime import datetime

print("=" * 70)
print("  📊 오늘 BREAKOUT 시뮬레이션 (ohlcv캐시 + price API)")
print("=" * 70)

# 실제 경과분 기준 (장 마감 후엔 390분으로 고정)
now = datetime.now()
elapsed_min = min(max((now.hour - 9) * 60 + now.minute, 1), 390)
print(f"\n  현재시각: {now.strftime('%H:%M')}  경과분: {elapsed_min}분 (오늘 전체 누적 기준)\n")

# 거래량 상위 50개
stocks = get_volume_rank(top_n=50)
print(f"  조회 종목: {len(stocks)}개 (등락률 3~25% 필터 후)\n")

EXCLUDE_KEYWORDS = ["ETF","ETN","KODEX","TIGER","KBSTAR","KOSEF",
                    "ARIRANG","HANARO","SOL","ACE","KoAct",
                    "리츠","REIT","스팩","SPAC"]

print(f"  종목별 상세 분석\n  {'─' * 60}")

passed = []

for s in stocks:
    code        = s["code"]
    name        = s["name"]
    change_rate = s.get("change_rate", 0)
    volume      = s.get("volume", 0)        # 당일 누적거래량
    trade_amt   = s.get("trade_amount", 0)  # 당일 누적거래대금

    # 제외 종목
    if any(kw in name for kw in EXCLUDE_KEYWORDS):
        print(f"  {name:<14}  제외종목")
        continue
    if name.endswith("우") or name.endswith("우B") or name.endswith("우C"):
        print(f"  {name:<14}  우선주제외")
        continue

    # 전일 OHLCV
    prev = get_prev_ohlcv(code)
    if not prev or prev["volume"] == 0:
        print(f"  {name:<14}  전일데이터없음")
        continue

    prev_vol       = prev["volume"]
    prev_high      = prev["high"]
    prev_trade_amt = prev["trade_amount"]

    # 분당환산 비율
    vol_surge = (volume / elapsed_min) / (prev_vol / 390) if prev_vol > 0 else 0
    amt_surge = (trade_amt / elapsed_min) / (prev_trade_amt / 390) if prev_trade_amt > 0 else 0

    # 오늘 고가 (현재가 API)
    detail     = get_current_price(code)
    today_high = detail.get("high", 0) if detail else 0
    cur_price  = s.get("price", 0)

    # 체결강도: inquire-ccnl tday_rltv (장 마감 후도 조회 가능)
    import requests as _req, time as _time
    _r2 = _req.get(
        f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-ccnl",
        headers=get_headers("FHKST01010300"),
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        timeout=3,
    )
    _items2  = _r2.json().get("output", [])
    exec_str = float(_items2[0].get("tday_rltv", 100.0)) if _items2 else 100.0
    s["exec_strength"] = exec_str
    _time.sleep(0.1)

    # 필터 체크
    failed_reasons = []
    if not (BREAKOUT["min_change_rate"] <= change_rate <= BREAKOUT["max_change_rate"]):
        failed_reasons.append(f"등락률범위외({change_rate:+.1f}%)")
    if amt_surge < ADVANCED_FILTER.get("trade_amount_surge_ratio", 2.0):
        failed_reasons.append(f"거래대금부족({amt_surge:.1f}배)")
    if vol_surge < BREAKOUT["volume_surge_ratio"]:
        failed_reasons.append(f"거래량부족({vol_surge:.1f}배)")
    if today_high <= prev_high:
        failed_reasons.append(f"전일고가미돌파(오늘고가{today_high:,}<=전일고가{prev_high:,})")

    if failed_reasons:
        print(f"\n  🔴 {name} ({change_rate:+.1f}%)")
        print(f"     탈락: {' | '.join(failed_reasons)}")
        continue

    # 점수 계산
    exec_str     = s.get("exec_strength", 100)
    str_score    = min(exec_str / BREAKOUT.get("exec_strength_max", 120.0), 1.0) * 30
    rate_score   = min(change_rate / BREAKOUT.get("change_rate_max", 15.0), 1.0) * 30
    rank_v       = s.get("volume_rank", 300)
    rank_a       = s.get("trade_rank", 300)
    rank_v_score = max(0, 20 - 20/300*(rank_v-1))
    rank_a_score = max(0, 20 - 20/300*(rank_a-1))
    score        = round(str_score + rate_score + rank_v_score + rank_a_score, 1)

    # 수익 시뮬레이션
    buy_price  = prev_high
    target     = round(buy_price * 1.05)
    stop       = round(buy_price * 0.97)
    if today_high >= target:
        sell_price = target
        result_str = "✅ 익절+5%"
    elif cur_price <= stop:
        sell_price = stop
        result_str = "❌ 손절-3%"
    else:
        sell_price = cur_price
        result_str = f"📌 현재가{cur_price:,}"

    profit_rate = round((sell_price / buy_price - 1) * 100, 2)
    shares      = (2_000_000 // 3) // buy_price
    pnl         = shares * (sell_price - buy_price)
    cutline     = "🟢 통과" if score >= 70 else "🟡 미달"

    print(f"\n  {'🟢' if score >= 70 else '🟡'} {name}  [{cutline}]")
    print(f"     등락률     {change_rate:>+6.1f}%")
    print(f"     거래량배수  {vol_surge:>6.1f}배  (전일분당 대비)")
    print(f"     거래대금배수 {amt_surge:>6.1f}배  (전일분당 대비)")
    print(f"     전일고가    {prev_high:>8,}원  →  오늘고가 {today_high:,}원")
    print(f"     체결강도    {exec_str:>6.1f}%")
    print(f"     ─────────────────────────────────────")
    print(f"     점수 분해   체결강도 {str_score:.1f}점 + 등락률 {rate_score:.1f}점 + 거래량순위 {rank_v_score:.1f}점 + 거래대금순위 {rank_a_score:.1f}점")
    print(f"     총점        {score}점  (커트라인 70점)")
    print(f"     매수가      {buy_price:,}원  →  익절목표 {target:,}원  손절선 {stop:,}원")
    print(f"     결과        {result_str}  수익률 {profit_rate:+.1f}%  손익 {pnl:+,}원  ({shares}주)")
    passed.append({"name":name,"score":score,"profit_rate":profit_rate,"pnl":pnl,"result":result_str})

# 요약
print(f"\n{'=' * 70}")
buy_signals = [p for p in passed if p["score"] >= 70]
print(f"  🎯 매수 신호: {len(buy_signals)}개  |  커트라인 70점")
total_pnl = sum(p["pnl"] for p in buy_signals)
for p in sorted(buy_signals, key=lambda x: -x["score"]):
    print(f"    {p['name']}  {p['score']}점  {p['result']}  수익률{p['profit_rate']:+.1f}%  손익{p['pnl']:+,}원")
if buy_signals:
    print(f"\n  💰 총 손익: {total_pnl:+,}원")
print(f"\n  완료  {datetime.now().strftime('%H:%M:%S')}")
