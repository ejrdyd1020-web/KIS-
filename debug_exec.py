"""체결강도 조회 API 탐색"""
import sys, os, requests
sys.path.insert(0, os.path.dirname(__file__))
from auth import get_access_token, get_headers, get_base_url
get_access_token()

base = get_base_url()

# 방법1: inquire-ccnl (체결 내역)
print("=== inquire-ccnl (체결내역) ===")
r = requests.get(
    f"{base}/uapi/domestic-stock/v1/quotations/inquire-ccnl",
    headers=get_headers("FHKST01010300"),
    params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "032820"},
    timeout=5,
)
d = r.json()
print(f"rt_cd={d.get('rt_cd')}  msg={d.get('msg1')}")
items = d.get("output", [])
if items:
    print("첫번째 항목 필드:")
    for k,v in items[0].items():
        print(f"  {k} = {v}")

# 방법2: inquire-asking-price-exp-ccn (호가/체결강도)
print("\n=== inquire-asking-price-exp-ccn ===")
r2 = requests.get(
    f"{base}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
    headers=get_headers("FHKST01010200"),
    params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "032820"},
    timeout=5,
)
d2 = r2.json()
print(f"rt_cd={d2.get('rt_cd')}  msg={d2.get('msg1')}")
o2 = d2.get("output2", {})
if o2:
    for k,v in o2.items():
        if any(x in k for x in ["cntg","seln","shnu","ntby","strength"]):
            print(f"  {k} = {v}")
