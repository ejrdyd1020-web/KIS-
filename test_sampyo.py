from dotenv import load_dotenv
load_dotenv()

from auth import get_access_token, get_headers, get_base_url
import requests, json

get_access_token()

# 삼표시멘트 종목코드 후보들 테스트
codes = [
    ("038500", "삼표시멘트 후보1"),
    ("004430", "삼표시멘트 후보2"),
    ("014440", "삼표시멘트 후보3"),
    ("003600", "삼표시멘트 후보4"),
]

for code, desc in codes:
    res = requests.get(
        get_base_url() + "/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=get_headers("FHKST01010100"),
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        timeout=5,
    )
    data = res.json()
    if data.get("rt_cd") == "0":
        name = data["output"].get("hts_kor_isnm", "")
        price = data["output"].get("stck_prpr", "0")
        print(f"[{code}] {name} | 현재가: {int(price):,}원")
    else:
        print(f"[{code}] {desc} - 조회 실패")
