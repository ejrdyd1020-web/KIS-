"""오늘 3/10 거래량 순위 + 체결강도 전체 조회"""
import sys, os, requests, time
sys.path.insert(0, os.path.dirname(__file__))
from auth import get_access_token, get_headers, get_base_url
get_access_token()

base = get_base_url()

# 거래량 순위 전체 조회
r = requests.get(
    f"{base}/uapi/domestic-stock/v1/quotations/volume-rank",
    headers=get_headers("FHPST01710000"),
    params={
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code" : "20171",
        "fid_input_iscd"        : "0000",
        "fid_div_cls_code"      : "0",
        "fid_blng_cls_code"     : "0",
        "fid_trgt_cls_code"     : "111111111",
        "fid_trgt_exls_cls_code": "111111",
        "fid_input_price_1"     : "",
        "fid_input_price_2"     : "",
        "fid_vol_cnt"           : "",
        "fid_input_date_1"      : "",
    },
    timeout=5,
)
items = r.json().get("output", [])

# 등락률 3~25% 필터
filtered = [i for i in items if 3.0 <= float(i.get("prdy_ctrt", 0)) <= 25.0]
print(f"전체 {len(items)}개 → 등락률 3~25% 필터 후 {len(filtered)}개\n")

print(f"  {'순위':>4} {'종목':<16} {'등락률':>7} {'체결강도':>8}")
print("  " + "-" * 45)

for item in filtered:
    code      = item.get("mksc_shrn_iscd", "")
    name      = item.get("hts_kor_isnm", "")
    rank      = item.get("data_rank", "-")
    chg       = float(item.get("prdy_ctrt", 0))

    # 체결강도 조회
    r2 = requests.get(
        f"{base}/uapi/domestic-stock/v1/quotations/inquire-ccnl",
        headers=get_headers("FHKST01010300"),
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        timeout=3,
    )
    items2    = r2.json().get("output", [])
    exec_str  = float(items2[0].get("tday_rltv", 0)) if items2 else 0
    time.sleep(0.1)  # API 호출 간격

    print(f"  {rank:>4}위 {name:<16} {chg:>+6.1f}%  {exec_str:>7.1f}%")
