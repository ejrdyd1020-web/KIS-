"""분봉 bulk 반복 호출 테스트"""
import sys, os, requests, time
sys.path.insert(0, os.path.dirname(__file__))
from auth import get_access_token, get_headers, get_base_url
get_access_token()

CODE = "005930"
base = get_base_url()
all_candles = []
last_time = ""

for i in range(5):
    params = {
        "fid_etc_cls_code"      : "",
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd"        : CODE,
        "fid_input_hour_1"      : last_time if last_time else "0",
        "fid_pw_data_incu_yn"   : "Y" if last_time else "N",
    }
    r = requests.get(
        f"{base}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers=get_headers("FHKST03010200"),
        params=params, timeout=5,
    )
    d = r.json()
    items = d.get("output2", [])
    print(f"  [{i+1}회] last_time={last_time!r}  items={len(items)}개", end="")
    if items:
        print(f"  첫봉={items[0].get('stck_cntg_hour')}  마지막봉={items[-1].get('stck_cntg_hour')}")
        all_candles.extend(items)
        last_time = items[-1].get("stck_cntg_hour", "")
    else:
        print()
        break
    if len(all_candles) >= 100:
        break
    time.sleep(0.2)

print(f"\n총 수집: {len(all_candles)}개")
