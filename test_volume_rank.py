from dotenv import load_dotenv
load_dotenv()

from auth import get_access_token, get_headers, get_base_url
import requests
import json

get_access_token()

res = requests.get(
    get_base_url() + "/uapi/domestic-stock/v1/quotations/volume-rank",
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
    }
)

data = res.json()
output = data.get("output", [])
print("결과 수:", len(output), "개")
print("rt_cd:", data.get("rt_cd"))
print("msg1:", data.get("msg1"))

if output:
    print("\n첫번째 종목 전체 필드:")
    print(json.dumps(output[0], indent=2, ensure_ascii=False))
