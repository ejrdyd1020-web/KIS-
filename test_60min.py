from dotenv import load_dotenv
import json, os, sys, requests
load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from auth import get_access_token, get_headers, get_base_url
get_access_token()

res = requests.get(
    f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
    headers=get_headers("FHKST03010200"),
    params={
        "fid_etc_cls_code"      : "",
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd"        : "005930",
        "fid_input_hour_1"      : "60",   # 60분봉
        "fid_pw_data_incu_yn"   : "Y",    # 과거 포함
    },
    timeout=5,
)
data = res.json()
print(f"rt_cd : {data.get('rt_cd')}")
print(f"msg1  : {data.get('msg1')}")
print(f"\n[60분봉 데이터 (최근 10개)]")
output2 = data.get("output2", [])
print(f"총 {len(output2)}개 반환")
for item in output2[:10]:
    print(f"  날짜: {item.get('stck_bsop_date','')} {item.get('stck_cntg_hour','')} | "
          f"종가: {item.get('stck_prpr','')} | "
          f"거래량: {item.get('cntg_vol','')}")
