# ============================================================
#  scripts/index_analyzer.py  –  KIS API 응답 필드 확인용
#
#  ※ 프로덕션 코드 아님 — 개발/디버깅 전용 스크립트
#     실제 지수 조회는 api/index.py 사용
#
#  실행: python scripts/index_analyzer.py
# ============================================================

from dotenv import load_dotenv
load_dotenv()

from auth import get_access_token, get_headers, get_base_url
import requests
import json

get_access_token()

# ── 코스피 지수 현재가 조회 (FHPUP02100000) ──────────────────
print("\n[코스피 지수 현재가]")
res = requests.get(
    get_base_url() + "/uapi/domestic-stock/v1/quotations/inquire-index-price",
    headers=get_headers("FHPUP02100000"),
    params={
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD"        : "0001",   # 0001=코스피, 1001=코스닥
    }
)
data = res.json()
print("rt_cd:", data.get("rt_cd"), "/ msg:", data.get("msg1"))
output = data.get("output", {})
if output:
    print(f"  지수: {output.get('bstp_nmix_prpr')}  등락률: {output.get('bstp_nmix_prdy_ctrt')}%")
    print("\n[전체 필드]")
    print(json.dumps(output, indent=2, ensure_ascii=False))

# ── 거래량 순위 조회 (FHPST01710000) — 필드 확인용 ──────────
print("\n\n[거래량 순위 — 첫 종목 필드 확인]")
res2 = requests.get(
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
data2 = res2.json()
output2 = data2.get("output", [])
print(f"결과 수: {len(output2)}개 / rt_cd: {data2.get('rt_cd')} / msg: {data2.get('msg1')}")
if output2:
    print("\n첫번째 종목 전체 필드:")
    print(json.dumps(output2[0], indent=2, ensure_ascii=False))
