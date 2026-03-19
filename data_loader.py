"""
data_loader.py
KIS API 일봉/분봉 데이터 조회 + CSV 캐시 관리

- 모의투자 서버(openapivts)는 시세조회 미지원
- 일봉/KOSPI200: 실서버(openapi) 강제 사용 (시세조회는 실서버도 무료)
- 분봉: 실서버 사용
- 토큰/인증: 기존 auth.py 그대로 사용
"""
import os
import sys
import time
import pandas as pd
from datetime import datetime, timedelta
import requests

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from auth import get_headers, APP_KEY, APP_SECRET, get_access_token

# 시세조회는 항상 실서버
REAL_URL = "https://openapi.koreainvestment.com:9443"

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# ─────────────────────────────────────────
# 공통 요청 (실서버 고정)
# ─────────────────────────────────────────
def _kis_request(tr_id: str, endpoint: str, params: dict, retries: int = 3) -> dict:
    url = f"{REAL_URL}{endpoint}"
    headers = {
        "content-type" : "application/json",
        "authorization": f"Bearer {get_access_token()}",
        "appkey"       : APP_KEY,
        "appsecret"    : APP_SECRET,
        "tr_id"        : tr_id,
    }
    for i in range(retries):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") == "0":
                return data
            print(f"[API 오류] {data.get('msg1')} (rt_cd={data.get('rt_cd')})")
            return {}
        except Exception as e:
            print(f"[요청 실패 {i+1}/{retries}] {e}")
            time.sleep(0.5)
    return {}


# ─────────────────────────────────────────
# KOSPI200 구성종목
# ─────────────────────────────────────────
def get_kospi200_codes() -> list:
    cache_path = os.path.join(CACHE_DIR, "kospi200_codes.csv")
    today = datetime.now().strftime("%Y%m%d")

    if os.path.exists(cache_path):
        df = pd.read_csv(cache_path, dtype=str)
        if not df.empty and "date" in df.columns and df["date"].iloc[0] == today:
            print(f"[KOSPI200] 캐시 사용: {len(df)}종목")
            return df["code"].tolist()

    data = _kis_request("FHPUP02100000",
        "/uapi/domestic-stock/v1/quotations/index-member",
        {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0028"})

    output = data.get("output2", [])
    if not output:
        print("[경고] KOSPI200 조회 실패 — 대표 종목 10개로 대체")
        return [
            "005930", "000660", "035420", "005380", "051910",
            "006400", "035720", "000270", "068270", "105560"
        ]

    codes = [item["mksc_shrn_iscd"] for item in output if item.get("mksc_shrn_iscd")]
    pd.DataFrame({"code": codes, "date": today}).to_csv(cache_path, index=False)
    print(f"[KOSPI200] {len(codes)}종목 조회 완료")
    return codes


# ─────────────────────────────────────────
# 일봉 데이터 (api/ohlcv.py 방식)
# ─────────────────────────────────────────
def get_daily_ohlcv(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    cache_path = os.path.join(CACHE_DIR, f"daily_{code}_{start_date}_{end_date}.csv")
    if os.path.exists(cache_path):
        return pd.read_csv(cache_path, parse_dates=["date"])

    data = _kis_request("FHKST01010400",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd"        : code,
            "fid_input_date_1"      : start_date,
            "fid_input_date_2"      : end_date,
            "fid_period_div_code"   : "D",
            "fid_org_adj_prc"       : "0",
        })

    rows = data.get("output2", [])
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "date"  : r.get("stck_bsop_date", ""),
        "open"  : int(r.get("stck_oprc", 0)),
        "high"  : int(r.get("stck_hgpr", 0)),
        "low"   : int(r.get("stck_lwpr", 0)),
        "close" : int(r.get("stck_clpr", 0)),
        "volume": int(r.get("acml_vol", 0)),
    } for r in rows])

    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(cache_path, index=False)
    return df


# ─────────────────────────────────────────
# 분봉 데이터 (api/chart.py 방식)
# ─────────────────────────────────────────
def get_minute_ohlcv(code: str, trade_date: str, interval: int = 5) -> pd.DataFrame:
    cache_path = os.path.join(CACHE_DIR, f"min{interval}_{code}_{trade_date}.csv")
    if os.path.exists(cache_path):
        return pd.read_csv(cache_path, parse_dates=["time"])

    all_candles = []
    seen_times = set()
    last_time = ""

    for _ in range(10):
        params = {
            "fid_etc_cls_code"      : "",
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd"        : code,
            "fid_input_hour_1"      : last_time if last_time else "0",
            "fid_pw_data_incu_yn"   : "Y" if last_time else "N",
        }
        data = _kis_request("FHKST03010200",
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            params, retries=2)
        items = data.get("output2", [])
        if not items:
            break

        new_added = False
        for item in items:
            t = item.get("stck_cntg_hour", "")
            if not t or t in seen_times:
                continue
            seen_times.add(t)
            all_candles.append({
                "time"  : f"{trade_date} {t[:2]}:{t[2:4]}:{t[4:6]}",
                "open"  : int(item.get("stck_oprc", 0)),
                "high"  : int(item.get("stck_hgpr", 0)),
                "low"   : int(item.get("stck_lwpr", 0)),
                "close" : int(item.get("stck_prpr", 0)),
                "volume": int(item.get("cntg_vol", 0)),
            })
            new_added = True

        if not new_added:
            break
        last_time = items[-1].get("stck_cntg_hour", "")
        time.sleep(0.2)

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    if interval > 1:
        df = df.set_index("time").resample(f"{interval}min").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna().reset_index()

    df.to_csv(cache_path, index=False)
    return df


# ─────────────────────────────────────────
# 거래일 리스트
# ─────────────────────────────────────────
def get_trading_days(start: str, end: str) -> list:
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    days = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return days
