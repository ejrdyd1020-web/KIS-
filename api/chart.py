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
        "fid_input_hour_1"      : "0",    # 현재시각 기준 최근 분봉
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


def get_minute_chart_bulk(stock_code: str, need: int = 120) -> list[dict]:
    """
    1분봉 데이터를 여러 번 호출해서 need개 이상 수집.
    KIS API는 1회 호출당 최대 ~30개 반환하므로
    마지막 캔들 시간을 기준으로 반복 호출해 누적.

    Returns:
        최신 데이터가 index 0인 캔들 리스트 (최소 need개 목표)
    """
    all_candles: list[dict] = []
    seen_times: set[str] = set()
    last_time = ""   # 빈 문자열 = 현재 시각부터

    tr_id = "FHKST03010200"

    for _ in range(10):   # 최대 10회 반복 (충분히 여유)
        params = {
            "fid_etc_cls_code"      : "",
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd"        : stock_code,
            "fid_input_hour_1"      : last_time if last_time else "0",
            "fid_pw_data_incu_yn"   : "Y" if last_time else "N",
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
        except Exception as e:
            logger.error(f"[{stock_code}] 분봉 벌크 조회 오류: {e}")
            break

        if data.get("rt_cd") != "0":
            break

        items = data.get("output2", [])
        if not items:
            break

        new_added = False
        for item in items:
            t = item.get("stck_cntg_hour", "")
            if not t or t in seen_times:
                continue
            seen_times.add(t)
            all_candles.append({
                "time"  : t,
                "open"  : int(item.get("stck_oprc", 0)),
                "high"  : int(item.get("stck_hgpr", 0)),
                "low"   : int(item.get("stck_lwpr", 0)),
                "close" : int(item.get("stck_prpr", 0)),
                "volume": int(item.get("cntg_vol", 0)),
            })
            new_added = True

        if not new_added or len(all_candles) >= need:
            break

        # 마지막 캔들 시간으로 다음 호출 기준 설정
        last_time = items[-1].get("stck_cntg_hour", "")
        if not last_time:
            break

        import time as _time
        _time.sleep(0.2)   # API 호출 간격

    return all_candles


def get_5min_chart(stock_code: str, need: int = 40) -> list[dict]:
    """
    1분봉 데이터를 5개씩 묶어 5분봉으로 변환.
    need개의 5분봉을 만들려면 need×5개의 1분봉이 필요.

    반환값: [{"open", "high", "low", "close", "volume"}, ...]
            최신 데이터가 index 0
    """
    raw = get_minute_chart_bulk(stock_code, need=need * 5 + 10)
    if len(raw) < 5:
        return []

    # 최신 → 과거 순서로 5개씩 그룹핑
    candles_5m = []
    for i in range(0, len(raw) - 4, 5):
        group = raw[i:i + 5]          # [최신, ..., 가장 오래된] 5개
        candles_5m.append({
            "open"  : group[-1]["open"],                        # 가장 오래된 봉의 시가
            "high"  : max(c["high"]   for c in group),
            "low"   : min(c["low"]    for c in group),
            "close" : group[0]["close"],                        # 가장 최신 봉의 종가
            "volume": sum(c["volume"] for c in group),
        })
    return candles_5m


def get_ma40_1min(stock_code: str) -> float:
    """
    1분봉 40이평선(MA40) 계산.
    최근 40개 1분봉 종가의 평균.

    Returns:
        MA40 값 (float), 데이터 부족 시 0.0
    """
    candles = get_minute_chart(stock_code, count=45)

    if len(candles) < 40:
        logger.warning(f"[{stock_code}] 분봉 데이터 부족 ({len(candles)}개, MA40 최소 40개 필요)")
        return 0.0

    closes = [c["close"] for c in candles[:40]]
    ma40   = sum(closes) / 40
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
