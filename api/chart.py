# ============================================================
#  api/chart.py  –  분봉 데이터 조회 (1분봉 기준)
# ============================================================

import requests
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from auth import get_headers, get_base_url
from utils.logger import get_logger

logger = get_logger("chart")


def get_minute_chart(stock_code: str, count: int = 30) -> list[dict]:
    """
    1분봉 데이터 조회 (최근 count개).

    반환값: [
        {
            "time"  : "093000",   # 체결시간
            "open"  : 74000,
            "high"  : 74500,
            "low"   : 73800,
            "close" : 74200,
            "volume": 12345,
        },
        ...  (최신 데이터가 index 0)
    ]
    """
    tr_id = "FHKST03010200"

    params = {
        "fid_etc_cls_code"      : "",
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd"        : stock_code,
        "fid_input_hour_1"      : "1",    # 1분봉
        "fid_pw_data_incu_yn"   : "N",
    }

    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=get_headers(tr_id),
            params=params,
            timeout=5,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"[{stock_code}] 분봉 조회 실패: {data.get('msg1')}")
            return []

        candles = []
        for item in data.get("output2", [])[:count]:
            candles.append({
                "time"  : item.get("stck_cntg_hour", ""),
                "open"  : int(item.get("stck_oprc", 0)),
                "high"  : int(item.get("stck_hgpr", 0)),
                "low"   : int(item.get("stck_lwpr", 0)),
                "close" : int(item.get("stck_prpr", 0)),
                "volume": int(item.get("cntg_vol", 0)),
            })

        return candles

    except Exception as e:
        logger.error(f"[{stock_code}] 분봉 조회 오류: {e}")
        return []


def get_ma40_1min(stock_code: str) -> float:
    """
    1분봉 40이평선(MA40) 계산.
    최근 40개 1분봉 종가의 평균.

    Returns:
        MA40 값 (float), 데이터 부족 시 0.0
    """
    candles = get_minute_chart(stock_code, count=45)

    if len(candles) < 20:
        logger.warning(f"[{stock_code}] 분봉 데이터 부족 ({len(candles)}개, MA40 최소 40개 필요)")
        return 0.0

    n = min(40, len(candles))
    closes = [c["close"] for c in candles[:n]]
    ma40   = sum(closes) / n
    return round(ma40, 2)


def get_volume_ratio_1min(stock_code: str) -> float:
    """
    1분 전 거래량 대비 현재 1분 거래량 비율 계산.

    Returns:
        비율 (%) 예: 500.0 = 500%
        직전봉 거래량이 0이면 0.0 반환
    """
    candles = get_minute_chart(stock_code, count=5)

    if len(candles) < 2:
        logger.warning(f"[{stock_code}] 분봉 데이터 부족")
        return 0.0

    cur_vol  = candles[0]["volume"]   # 현재 1분봉 거래량
    prev_vol = candles[1]["volume"]   # 직전 1분봉 거래량

    if prev_vol <= 0:
        return 0.0

    ratio = cur_vol / prev_vol * 100
    return round(ratio, 1)
