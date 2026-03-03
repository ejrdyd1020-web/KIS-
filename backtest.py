# ============================================================
#  backtest.py  –  과거 분봉 데이터 기반 백테스트
#  대상일: 2026-02-24 / 기준: 1분봉 MA40 손절 + 익절 +5%
# ============================================================

from dotenv import load_dotenv
import os, json
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import requests
from datetime import datetime
from auth import get_access_token, get_headers, get_base_url
from config import STOP_LOSS_PCT, TAKE_PROFIT_PCT

get_access_token()

BASE_URL = get_base_url()

TARGETS = [
    {"code": "000660", "name": "SK하이닉스"},
]

TARGET_DATE = "20260225"
BUY_TIME    = "0900"


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


def calc_ma40(candles, idx):
    if idx < 40:
        return None
    closes = [candles[i]["close"] for i in range(idx - 40, idx)]
    return sum(closes) / 40


def run_backtest(code, name, candles):
    if not candles:
        return {"name": name, "result": "데이터없음"}

    buy_candle = next((c for c in candles if c["time"] >= BUY_TIME), None)
    if not buy_candle:
        return {"name": name, "result": "매수시점없음"}

    buy_price   = buy_candle["open"] or buy_candle["close"]
    stop_loss   = int(buy_price * (1 + STOP_LOSS_PCT   / 100))
    take_profit = int(buy_price * (1 + TAKE_PROFIT_PCT / 100))
    buy_idx     = candles.index(buy_candle)

    print(f"\n  [{name}({code})]")
    print(f"  매수가: {buy_price:,}원 | 손절가: {stop_loss:,}원({STOP_LOSS_PCT:+.1f}%) | 익절가: {take_profit:,}원(+{TAKE_PROFIT_PCT:.1f}%)")

    sell_price  = None
    sell_time   = None
    sell_reason = None

    for i in range(buy_idx + 1, len(candles)):
        c     = candles[i]
        price = c["close"]
        time  = c["time"]
        ma40  = calc_ma40(candles, i)

        if time >= "1520":
            sell_price = price; sell_time = time; sell_reason = "장마감"; break
        if price <= stop_loss:
            sell_price = price; sell_time = time; sell_reason = "고정손절"; break
        if price >= take_profit:
            sell_price = price; sell_time = time; sell_reason = "익절"; break
        if ma40 and price < ma40:
            sell_price = price; sell_time = time; sell_reason = f"MA40손절(MA40:{ma40:,.0f})"; break

    if not sell_price:
        sell_price  = candles[-1]["close"]
        sell_time   = candles[-1]["time"]
        sell_reason = "장마감"

    profit     = sell_price - buy_price
    profit_pct = profit / buy_price * 100
    sign       = "+" if profit >= 0 else ""

    print(f"  매도: {sell_time[:2]}:{sell_time[2:]} | 매도가: {sell_price:,}원 | 사유: {sell_reason} | 수익: {sign}{profit_pct:.2f}%")

    return {
        "name"       : name,
        "code"       : code,
        "buy_price"  : buy_price,
        "sell_price" : sell_price,
        "sell_time"  : sell_time,
        "sell_reason": sell_reason,
        "profit"     : profit,
        "profit_pct" : profit_pct,
    }


def main():
    print(f"""
{'='*60}
  📊 백테스트 - {TARGET_DATE[:4]}.{TARGET_DATE[4:6]}.{TARGET_DATE[6:]}
  매수: 9시 시가 / 손절: MA40 이탈 or {STOP_LOSS_PCT:+.1f}%
  익절: +{TAKE_PROFIT_PCT:.1f}% / 강제청산: 15:20
{'='*60}""")

    results = []
    for t in TARGETS:
        print(f"\n  [{t['name']}] 분봉 데이터 조회 중...")
        candles = get_minute_candles(t["code"], TARGET_DATE)
        print(f"  → {len(candles)}개 분봉 확인")

        if len(candles) < 5:
            print(f"  → 데이터 부족 - 스킵")
            continue

        result = run_backtest(t["code"], t["name"], candles)
        results.append(result)

    print(f"\n{'='*60}")
    print(f"  📋 백테스트 최종 결과")
    print(f"{'='*60}")

    total_profit_pct = 0
    wins = losses = 0

    for r in results:
        if "profit_pct" not in r:
            continue
        pct  = r["profit_pct"]
        sign = "+" if pct >= 0 else ""
        icon = "🟢" if pct >= 0 else "🔴"
        print(f"  {icon} {r['name']:12} | {sign}{pct:.2f}% | {r['sell_reason']}")
        total_profit_pct += pct
        wins += 1 if pct >= 0 else 0
        losses += 1 if pct < 0 else 0

    total = wins + losses
    if total > 0:
        print(f"\n  승률    : {wins}/{total} ({wins/total*100:.0f}%)")
        print(f"  평균수익: {total_profit_pct/total:+.2f}%")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
