# ============================================================
#  backtest.py  –  과거 분봉 데이터 기반 백테스트
#  대상일: 2026-02-27 / 조건검색 통과 종목 자동 백테스트
#  기준: 1분봉 MA40 손절 + 익절 +5%
#
#  실행: python backtest.py
# ============================================================

from dotenv import load_dotenv
import os
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import requests
import time
from datetime import datetime
from auth import get_access_token, get_headers, get_base_url
from config import STOP_LOSS_PCT, TAKE_PROFIT_PCT, CONDITION, ADVANCED_FILTER

get_access_token()

BASE_URL = get_base_url()

# ── 백테스트 설정 ─────────────────────────────────────────────
TARGET_DATE = "20260227"   # 백테스트 날짜
BUY_TIME    = "0900"       # 매수 시작 시간


# ── 분봉 데이터 조회 ──────────────────────────────────────────
def get_minute_candles(stock_code, date):
    all_candles = []
    end_time = "160000"
    seen_times = set()

    while True:
        res = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=get_headers("FHKST03010200"),
            params={
                "fid_etc_cls_code"      : "",
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd"        : stock_code,
                "fid_input_hour_1"      : end_time,
                "fid_pw_data_incu_yn"   : "Y",
            }, timeout=10,
        )
        if res.status_code != 200:
            break
        data = res.json()
        if data.get("rt_cd") != "0":
            break
        candles = data.get("output2", [])
        if not candles:
            break

        new_added = False
        for c in candles:
            if c.get("stck_bsop_date", "") != date:
                continue
            t = c.get("stck_cntg_hour", "")
            if t in seen_times:
                continue
            seen_times.add(t)
            all_candles.append({
                "time"  : t,
                "open"  : int(c.get("stck_oprc", 0)),
                "high"  : int(c.get("stck_hgpr", 0)),
                "low"   : int(c.get("stck_lwpr", 0)),
                "close" : int(c.get("stck_prpr", 0)),
                "volume": int(c.get("cntg_vol", 0)),
            })
            new_added = True

        if not new_added:
            break

        last_time = candles[-1].get("stck_cntg_hour", "")
        if not last_time or last_time >= end_time:
            break
        end_time = last_time

    all_candles.sort(key=lambda x: x["time"])
    return all_candles


# ── 2월 27일 거래량 상위 종목 조회 ───────────────────────────
def get_target_stocks():
    """당일 거래량 상위 종목 중 조건 필터 적용"""
    print("  📡 거래량 상위 종목 조회 중...")
    try:
        res = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=get_headers("FHPST01710000"),
            params={
                "fid_cond_mrkt_div_code" : "J",
                "fid_cond_scr_div_code"  : "20171",
                "fid_input_iscd"         : "0000",
                "fid_rank_sort_cls_code" : "0",
                "fid_input_cnt_1"        : "0",
                "fid_prc_cls_code"       : "0",
                "fid_input_price_1"      : "",
                "fid_input_price_2"      : "",
                "fid_vol_cnt"            : "",
                "fid_trgt_cls_code"      : "111111111",
                "fid_trgt_exls_cls_code" : "000000",
                "fid_div_cls_code"       : "0",
            },
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            print(f"  ❌ 조회 실패: {data.get('msg1')}")
            return []

        results = []
        for item in data.get("output", [])[:50]:
            change_rate = float(item.get("prdy_ctrt", 0))

            # 등락률 필터 (HTS 기준 7~25%)
            if not (CONDITION["min_change_rate"] <= change_rate <= CONDITION["max_change_rate"]):
                continue

            price = int(item.get("stck_prpr", 0))
            if price < CONDITION["min_price"]:
                continue

            results.append({
                "code"       : item.get("mksc_shrn_iscd", ""),
                "name"       : item.get("hts_kor_isnm", ""),
                "price"      : price,
                "change_rate": change_rate,
                "volume"     : int(item.get("acml_vol", 0)),
            })

        print(f"  → 조건 통과 종목: {len(results)}개")
        return results

    except Exception as e:
        print(f"  ❌ 오류: {e}")
        return []


# ── MA40 계산 ─────────────────────────────────────────────────
def calc_ma40(candles, idx):
    if idx < 40:
        return None
    closes = [candles[i]["close"] for i in range(idx - 40, idx)]
    return sum(closes) / 40


# ── 단일 종목 백테스트 ────────────────────────────────────────
def run_backtest(code, name, candles):
    if not candles:
        return None

    # 9시 이후 첫 번째 캔들에서 매수
    buy_candle = next((c for c in candles if c["time"] >= BUY_TIME), None)
    if not buy_candle:
        return None

    buy_price   = buy_candle["open"] or buy_candle["close"]
    if buy_price <= 0:
        return None

    stop_loss   = int(buy_price * (1 + STOP_LOSS_PCT   / 100))
    take_profit = int(buy_price * (1 + TAKE_PROFIT_PCT / 100))
    buy_idx     = candles.index(buy_candle)

    sell_price  = None
    sell_time   = None
    sell_reason = None

    for i in range(buy_idx + 1, len(candles)):
        c     = candles[i]
        price = c["close"]
        t     = c["time"]
        ma40  = calc_ma40(candles, i)

        if t >= "1520":
            sell_price = price; sell_time = t; sell_reason = "장마감"; break
        if price <= stop_loss:
            sell_price = price; sell_time = t; sell_reason = "고정손절"; break
        if price >= take_profit:
            sell_price = price; sell_time = t; sell_reason = "익절"; break
        if ma40 and price < ma40:
            sell_price = price; sell_time = t; sell_reason = f"MA40손절"; break

    if not sell_price:
        sell_price  = candles[-1]["close"]
        sell_time   = candles[-1]["time"]
        sell_reason = "장마감"

    profit_pct = (sell_price - buy_price) / buy_price * 100

    return {
        "name"       : name,
        "code"       : code,
        "buy_price"  : buy_price,
        "buy_time"   : buy_candle["time"],
        "sell_price" : sell_price,
        "sell_time"  : sell_time,
        "sell_reason": sell_reason,
        "profit_pct" : profit_pct,
        "profit"     : sell_price - buy_price,
    }


# ── 메인 ─────────────────────────────────────────────────────
def main():
    print(f"""
{'='*60}
  📊 백테스트 - {TARGET_DATE[:4]}.{TARGET_DATE[4:6]}.{TARGET_DATE[6:]}
  조건: 등락률 {CONDITION['min_change_rate']}~{CONDITION['max_change_rate']}%
  손절: MA40 이탈 or {STOP_LOSS_PCT:+.1f}% / 익절: +{TAKE_PROFIT_PCT:.1f}%
  강제청산: 15:20
{'='*60}""")

    # 조건 통과 종목 조회
    targets = get_target_stocks()

    if not targets:
        print("\n  ❌ 백테스트 대상 종목 없음")
        return

    print(f"\n  총 {len(targets)}개 종목 백테스트 시작...\n")

    results = []
    for t in targets:
        code = t["code"]
        name = t["name"]
        print(f"  [{name}({code})] 분봉 조회 중...", end=" ")

        candles = get_minute_candles(code, TARGET_DATE)
        print(f"{len(candles)}개 캔들")

        if len(candles) < 10:
            print(f"    → 데이터 부족 스킵")
            continue

        result = run_backtest(code, name, candles)
        if result:
            results.append(result)
            sign = "+" if result["profit_pct"] >= 0 else ""
            icon = "🟢" if result["profit_pct"] >= 0 else "🔴"
            print(f"    {icon} 매수 {result['buy_time'][:2]}:{result['buy_time'][2:]} "
                  f"→ 매도 {result['sell_time'][:2]}:{result['sell_time'][2:]} | "
                  f"{sign}{result['profit_pct']:.2f}% | {result['sell_reason']}")

        time.sleep(0.3)

    # ── 결과 요약 ─────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  📋 백테스트 최종 결과 ({TARGET_DATE[:4]}.{TARGET_DATE[4:6]}.{TARGET_DATE[6:]})")
    print(f"{'='*60}")

    if not results:
        print("  결과 없음")
        return

    results.sort(key=lambda x: x["profit_pct"], reverse=True)

    wins   = [r for r in results if r["profit_pct"] >= 0]
    losses = [r for r in results if r["profit_pct"] < 0]
    total  = len(results)
    avg    = sum(r["profit_pct"] for r in results) / total

    print(f"  {'종목명':<14} {'매수시각':>6} {'매도시각':>6} {'수익률':>8}  사유")
    print(f"  {'-'*55}")
    for r in results:
        sign = "+" if r["profit_pct"] >= 0 else ""
        icon = "🟢" if r["profit_pct"] >= 0 else "🔴"
        bt   = r["buy_time"]
        st   = r["sell_time"]
        print(f"  {icon} {r['name']:<13} "
              f"{bt[:2]}:{bt[2:]:>4}  {st[:2]}:{st[2:]:>4}  "
              f"{sign}{r['profit_pct']:>6.2f}%  {r['sell_reason']}")

    print(f"\n  총 종목  : {total}개")
    print(f"  익절     : {len(wins)}개")
    print(f"  손절/청산: {len(losses)}개")
    print(f"  승률     : {len(wins)/total*100:.1f}%")
    print(f"  평균수익 : {avg:+.2f}%")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
