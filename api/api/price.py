# ============================================================
#  api/price.py  –  현재가 조회 / 등락률 순위 조회
# ============================================================

import requests
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from auth import get_headers, get_base_url
from utils.logger import get_logger
from config import CONDITION

logger = get_logger("price")


def get_current_price(stock_code: str) -> dict:
    """
    단일 종목 현재가 조회.

    반환값:
        {
            "code"        : "005930",
            "name"        : "삼성전자",
            "price"       : 75000,
            "open"        : 74000,
            "high"        : 75500,
            "low"         : 73800,
            "volume"      : 1234567,
            "change_rate" : 1.35,     # 등락률 (%)
            "change_amt"  : 1000,     # 등락금액
        }
    """
    tr_id = "FHKST01010100"  # 실전/모의 공통

    params = {
        "fid_cond_mrkt_div_code": "J",   # 주식
        "fid_input_iscd"        : stock_code,
    }

    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=get_headers(tr_id),
            params=params,
            timeout=5,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"[{stock_code}] 현재가 조회 실패: {data.get('msg1')}")
            return {}

        o = data["output"]
        return {
            "code"        : stock_code,
            "name"        : o.get("hts_kor_isnm", ""),
            "price"       : int(o.get("stck_prpr", 0)),
            "open"        : int(o.get("stck_oprc", 0)),
            "high"        : int(o.get("stck_hgpr", 0)),
            "low"         : int(o.get("stck_lwpr", 0)),
            "volume"      : int(o.get("acml_vol", 0)),
            "change_rate" : float(o.get("prdy_ctrt", 0)),
            "change_amt"  : int(o.get("prdy_vrss", 0)),
        }

    except Exception as e:
        logger.error(f"[{stock_code}] 현재가 조회 오류: {e}")
        return {}


def get_fluctuation_rank(
    market   : str = "0",   # 0:전체, 1:코스피, 2:코스닥
    sort_type: str = "1",   # 1:상승률, 2:하락률
    top_n    : int = 30,
) -> list[dict]:
    """
    등락률 순위 조회 (조건 필터링 포함).

    반환값: [
        {"code": "005930", "name": "삼성전자", "price": 75000,
         "change_rate": 3.5, "volume": 1234567},
        ...
    ]
    """
    tr_id = "FHPST01700000"

    params = {
        "fid_cond_mrkt_div_code" : "J",
        "fid_cond_scr_div_code"  : "20170",
        "fid_input_iscd"         : market,
        "fid_rank_sort_cls_code" : sort_type,
        "fid_input_cnt_1"        : "0",
        "fid_prc_cls_code"       : "1",
        "fid_input_price_1"      : str(CONDITION["min_price"]),
        "fid_input_price_2"      : str(CONDITION["max_price"]),
        "fid_vol_cnt"            : str(CONDITION["min_volume"]),
        "fid_trgt_cls_code"      : "0",
        "fid_trgt_exls_cls_code" : "0",
        "fid_div_cls_code"       : "0",
        "fid_rsfl_rate1"         : str(CONDITION["min_change_rate"]),
        "fid_rsfl_rate2"         : str(CONDITION["max_change_rate"]),
    }

    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/ranking/fluctuation",
            headers=get_headers(tr_id),
            params=params,
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"등락률 순위 조회 실패: {data.get('msg1')}")
            return []

        results = []
        for item in data.get("output", [])[:top_n]:
            results.append({
                "code"        : item.get("stck_shrn_iscd", ""),
                "name"        : item.get("hts_kor_isnm", ""),
                "price"       : int(item.get("stck_prpr", 0)),
                "change_rate" : float(item.get("prdy_ctrt", 0)),
                "change_amt"  : int(item.get("prdy_vrss", 0)),
                "volume"      : int(item.get("acml_vol", 0)),
            })

        logger.info(f"등락률 순위 조회 완료: {len(results)}개 종목")
        return results

    except Exception as e:
        logger.error(f"등락률 순위 조회 오류: {e}")
        return []


def get_multiple_prices(stock_codes: list[str]) -> dict[str, dict]:
    """
    여러 종목 현재가 일괄 조회.
    반환값: {"005930": {...}, "000660": {...}}
    """
    result = {}
    for code in stock_codes:
        info = get_current_price(code)
        if info:
            result[code] = info
    return result


# ── 테스트 ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n[1] 삼성전자 현재가 조회")
    price = get_current_price("005930")
    if price:
        print(f"  종목명  : {price['name']}")
        print(f"  현재가  : {price['price']:,}원")
        print(f"  등락률  : {price['change_rate']:+.2f}%")
        print(f"  거래량  : {price['volume']:,}")
    else:
        print("  조회 실패")

    print("\n[2] 등락률 상위 종목 (상위 10개)")
    ranks = get_fluctuation_rank(top_n=10)
    if ranks:
        print(f"  {'순위':<4} {'종목명':<16} {'현재가':>8} {'등락률':>7} {'거래량':>12}")
        print(f"  {'-'*52}")
        for i, r in enumerate(ranks, 1):
            print(f"  {i:<4} {r['name']:<16} {r['price']:>8,} {r['change_rate']:>+6.2f}% {r['volume']:>12,}")
    else:
        print("  조회 실패 (장 마감 시간이거나 모의투자 미지원)")
