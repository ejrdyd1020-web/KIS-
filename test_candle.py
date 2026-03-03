from dotenv import load_dotenv
import os, json
load_dotenv()

from auth import get_access_token, get_headers, get_base_url
import requests

get_access_token()

# SK하이닉스 분봉 조회 테스트
res = requests.get(
    get_base_url() + "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
    headers=get_headers("FHKST03010200"),
    params={
        "fid_etc_cls_code"      : "",
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd"        : "000660",
        "fid_input_hour_1"      : "160000",
        "fid_pw_data_incu_yn"   : "Y",
    },
    timeout=10,
)

print("HTTP:", res.status_code)
data = res.json()
print("rt_cd:", data.get("rt_cd"))
print("msg1:", data.get("msg1"))

output2 = data.get("output2", [])
print("분봉 수:", len(output2))
if output2:
    print("첫번째:", json.dumps(output2[0], ensure_ascii=False))
    print("마지막:", json.dumps(output2[-1], ensure_ascii=False))
