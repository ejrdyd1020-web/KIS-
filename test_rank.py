from dotenv import load_dotenv
load_dotenv()

from auth import get_access_token, get_headers, get_base_url
import requests
import json

get_access_token()

res = requests.get(
    get_base_url() + "/uapi/domestic-stock/v1/ranking/fluctuation",
    headers=get_headers("FHPST01700000"),
    params={
        "fid_cond_mrkt_div_code" : "J",
        "fid_cond_scr_div_code"  : "20170",
        "fid_input_iscd"         : "0",
        "fid_rank_sort_cls_code" : "1",
        "fid_input_cnt_1"        : "0",
        "fid_prc_cls_code"       : "0",
        "fid_input_price_1"      : "0",
        "fid_input_price_2"      : "0",
        "fid_vol_cnt"            : "0",
        "fid_trgt_cls_code"      : "0",
        "fid_trgt_exls_cls_code" : "0",
        "fid_div_cls_code"       : "0",
        "fid_rsfl_rate1"         : "0",
        "fid_rsfl_rate2"         : "0",
    }
)

data = res.json()
output = data.get("output", [])
print("결과 수:", len(output), "개")
print("rt_cd:", data.get("rt_cd"))
print("msg1:", data.get("msg1"))

if output:
    print("\n첫번째 종목:")
    print(json.dumps(output[0], indent=2, ensure_ascii=False))
else:
    print("\n결과 없음 - 모의투자 미지원 가능성 있음")
