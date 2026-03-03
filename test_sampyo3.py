from dotenv import load_dotenv
load_dotenv()

from auth import get_access_token, get_headers, get_base_url
import requests, json

get_access_token()

# 삼표시멘트 현재가 조회 - 오늘 거래량 확인
res = requests.get(
    get_base_url() + "/uapi/domestic-stock/v1/quotations/inquire-price",
    headers=get_headers("FHKST01010100"),
    params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "038500"},
    timeout=5,
)
data = res.json()
o = data.get("output", {})
print(f"종목명  : {o.get('hts_kor_isnm')}")
print(f"현재가  : {int(o.get('stck_prpr',0)):,}원")
print(f"등락률  : {o.get('prdy_ctrt')}%")
print(f"오늘거래량: {int(o.get('acml_vol',0)):,}주")
print(f"거래대금 : {int(o.get('acml_tr_pbmn',0)):,}원")
