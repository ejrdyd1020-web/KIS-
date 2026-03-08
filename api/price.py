# ============================================================
#  api/price.py  –  현재가 / 거래량순위 / 호가 / 체결강도 조회
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
    """단일 종목 현재가 + 52주 신고가 + 시가총액 + 체결강도 조회"""
    tr_id = "FHKST01010100"
    params = {
        "fid_cond_mrkt_div_code": "J",
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

        # 체결강도: 매수체결건수 / 매도체결건수 × 100
        buy_qty  = int(o.get("shnu_cntg_csnu", 0))
        sell_qty = int(o.get("seln_cntg_csnu", 0))
        exec_strength = round(buy_qty / sell_qty * 100, 2) if sell_qty > 0 else 100.0

        return {
            "code"          : stock_code,
            "name"          : o.get("hts_kor_isnm", ""),
            "price"         : int(o.get("stck_prpr", 0)),
            "open"          : int(o.get("stck_oprc", 0)),
            "high"          : int(o.get("stck_hgpr", 0)),
            "low"           : int(o.get("stck_lwpr", 0)),
            "volume"        : int(o.get("acml_vol", 0)),
            "prev_volume"   : int(o.get("avrg_vol", 0)),
            "change_rate"   : float(o.get("prdy_ctrt", 0)),
            "change_amt"    : int(o.get("prdy_vrss", 0)),
            "week52_high"   : int(o.get("d52_hgpr", 0)),
            "week52_low"    : int(o.get("d52_lwpr", 0)),
            "market_cap"    : int(o.get("hts_avls", 0)),
            "exec_strength" : exec_strength,
        }
    except Exception as e:
        logger.error(f"[{stock_code}] 현재가 조회 오류: {e}")
        return {}


def get_asking_price(stock_code: str) -> dict:
    """호가 잔량 조회 → 매수/매도 잔량 비율 계산"""
    tr_id = "FHKST01010200"
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd"        : stock_code,
    }
    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            headers=get_headers(tr_id),
            params=params,
            timeout=5,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            return {}

        o         = data.get("output1", {})
        total_bid = int(o.get("total_bidrem_qty", 0))
        total_ask = int(o.get("total_askp_rsqn", 0))
        total     = total_bid + total_ask
        bid_ratio = (total_bid / total * 100) if total > 0 else 50.0

        return {
            "bid_ratio" : round(bid_ratio, 1),
            "total_bid" : total_bid,
            "total_ask" : total_ask,
        }
    except Exception as e:
        logger.error(f"[{stock_code}] 호가 조회 오류: {e}")
        return {}


def get_volume_rank(top_n: int = 30) -> list[dict]:
    """
    거래량 순위 조회 (모의투자 지원).
    등락률 조건은 get_fluctuation_rank 대신 이 함수로 대체.
    """
    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/quotations/volume-rank",
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
                # 제외종목 설정 (6자리):
                #   1자리: 관리종목  2자리: 투자위험/경고/주의
                #   3자리: 우선주    4자리: 증거금100%
                #   5자리: ETF       6자리: 불성실공시
                "fid_trgt_exls_cls_code" : "111111",
                "fid_div_cls_code"       : "0",
            },
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"거래량 순위 조회 실패: {data.get('msg1')}")
            return []

        results = []
        for item in data.get("output", [])[:top_n]:
            change_rate = float(item.get("prdy_ctrt", 0))

            # 등락률 기본 필터 적용
            if not (CONDITION["min_change_rate"] <= change_rate <= CONDITION["max_change_rate"]):
                continue

            results.append({
                "code"         : item.get("mksc_shrn_iscd", ""),
                "name"         : item.get("hts_kor_isnm", ""),
                "price"        : int(item.get("stck_prpr", 0)),
                "change_rate"  : change_rate,
                "change_amt"   : int(item.get("prdy_vrss", 0)),
                "volume"       : int(item.get("acml_vol", 0)),
                "trade_amount"      : int(item.get("acml_tr_pbmn", 0)),  # 당일 누적거래대금 (원)
                "prev_trade_amount" : int(item.get("avrg_tr_pbmn", 0)),  # 전일 거래대금 (원)
                "prev_volume"       : int(item.get("prdy_vol", 0)),       # 전일 거래량
            })

        logger.info(f"거래량 순위 조회 완료: {len(results)}개 종목 (등락률 필터 후)")
        return results

    except Exception as e:
        logger.error(f"거래량 순위 조회 오류: {e}")
        return []


# 하위 호환성 유지
def get_fluctuation_rank(top_n: int = 30, **kwargs) -> list[dict]:
    """등락률 순위 대신 거래량 순위로 대체 (모의투자 호환)"""
    return get_volume_rank(top_n=top_n)


def get_multiple_prices(stock_codes: list[str]) -> dict[str, dict]:
    """여러 종목 현재가 일괄 조회"""
    result = {}
    for code in stock_codes:
        info = get_current_price(code)
        if info:
            result[code] = info
    return result
