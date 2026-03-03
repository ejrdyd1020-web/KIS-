from dotenv import load_dotenv
load_dotenv()

from auth import get_access_token, get_headers, get_base_url
import requests, json

get_access_token()

# 삼표시멘트 분봉 조회 테스트
for mkt in ["J", "Q"]:
    res = requests.get(
        get_base_url() + "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers=get_headers("FHKST03010200"),
        params={
            "fid_etc_cls_code"      : "",
            "fid_cond_mrkt_div_code": mkt,
            "fid_input_iscd"        : "038500",
            "fid_input_hour_1"      : "160000",
            "fid_pw_data_incu_yn"   : "Y",
        },
        timeout=10,
    )
    data = res.json()
    output2 = data.get("output2", [])
    print(f"[시장:{mkt}] HTTP:{res.status_code} | 분봉수:{len(output2)} | msg:{data.get('msg1')}")
    if output2:
        print("  첫번째:", json.dumps(output2[0], ensure_ascii=False))
