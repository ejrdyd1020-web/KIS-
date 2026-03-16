"""전일 OHLCV 필드명 전체 확인"""
import sys, os, requests
sys.path.insert(0, os.path.dirname(__file__))
from auth import get_access_token, get_headers, get_base_url
get_access_token()

r = requests.get(
    f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-price",
    headers=get_headers("FHKST01010100"),
    params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "006910"},
    timeout=5,
)
o = r.json().get("output", {})
# 전일 관련 필드만 출력
for k, v in o.items():
    if any(x in k for x in ["prdy", "sdpr", "mxpr", "llam", "oprc", "acml"]):
        print(f"  {k} = {v}")
