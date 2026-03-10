# ============================================================
#  api/ohlcv.py  –  전일 OHLCV 캐시 관리
#
#  역할:
#    1. KIS API로 종목별 전일 OHLCV 조회
#    2. data/ohlcv_prev.json 파일로 캐시 저장/로드
#    3. strategy_breakout / strategy_reversion에서 캐시 조회
#
#  호출 흐름:
#    premarket.py (08:30~09:00)
#      └─ fetch_and_save_ohlcv(codes)  ← 전 종목 일괄 수집 후 저장
#
#    strategy_breakout.py / strategy_reversion.py
#      └─ get_prev_ohlcv(code)         ← 캐시에서 즉시 반환
#
#  KIS API: FHKST01010100 (주식 일봉 조회)
# ============================================================

import os
import json
import time
import requests
import sys
from datetime import date, timedelta, datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from auth         import get_headers, get_base_url
from utils.logger import get_logger

logger = get_logger("ohlcv")

# ── 캐시 파일 경로 ────────────────────────────────────────────
_DATA_DIR   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_CACHE_PATH = os.path.join(_DATA_DIR, "ohlcv_prev.json")

# ── 메모리 캐시 (프로세스 내 재조회 방지) ────────────────────
_cache: dict = {}   # { code: {open, high, low, close, volume, trade_amount} }
_cache_date: str = ""


# ══════════════════════════════════════════
# 내부 유틸
# ══════════════════════════════════════════

def _ensure_data_dir():
    """data/ 디렉터리 없으면 생성"""
    os.makedirs(_DATA_DIR, exist_ok=True)


def _prev_business_day() -> str:
    """
    직전 영업일 반환 (토→금, 일→금, 평일→전일).
    공휴일은 별도 처리 없음 (단순 주말 제거).
    """
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:   # 5=토, 6=일
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


# ══════════════════════════════════════════
# KIS API — 단일 종목 전일 OHLCV 조회
# ══════════════════════════════════════════

def fetch_prev_ohlcv_single(code: str) -> dict | None:
    """
    KIS inquire-price API로 종목 전일 OHLCV 1건 조회.
    output에 전일 고/저/종가, 거래량, 거래대금이 포함되어 있어
    코스피·코스닥 모든 종목에서 안정적으로 작동.

    Returns:
        {"open", "high", "low", "close", "volume", "trade_amount"} or None
    """
    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=get_headers("FHKST01010100"),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd"        : code,
            },
            timeout=5,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"[{code}] OHLCV 조회 실패: {data.get('msg1')}")
            return None

        o = data.get("output", {})
        if not o:
            return None

        return {
            "open"        : int(o.get("stck_sdpr", 0)),   # 전일 종가로 대체 (전일 시가 필드 없음)
            "high"        : int(o.get("stck_mxpr", 0)),   # 전일 고가
            "low"         : int(o.get("stck_llam",  0)),  # 전일 저가
            "close"       : int(o.get("stck_sdpr", 0)),   # 전일 종가
            "volume"      : int(o.get("acml_vol",  0)),   # 전일 거래량
            "trade_amount": int(o.get("acml_tr_pbmn", 0)),# 전일 거래대금(원)
        }

    except Exception as e:
        logger.error(f"[{code}] OHLCV API 오류: {e}")
        return None


# ══════════════════════════════════════════
# 일괄 수집 + 캐시 저장
# ══════════════════════════════════════════

def fetch_and_save_ohlcv(codes: list[str], delay_sec: float = 0.2) -> int:
    """
    종목 리스트 전체 전일 OHLCV 수집 후 캐시 저장.

    Args:
        codes    : 종목코드 리스트
        delay_sec: API 호출 간격 (기본 0.2초)

    Returns:
        저장 성공 건수

    사용처:
        premarket.py — 장전 스캔 시 watchlist 대상 일괄 수집
        main.py      — 장 시작 전 보완 수집
    """
    _ensure_data_dir()
    global _cache, _cache_date

    today  = date.today().isoformat()
    result = {}
    ok_cnt = 0

    logger.info(f"전일 OHLCV 수집 시작: {len(codes)}개 종목")

    for i, code in enumerate(codes, 1):
        ohlcv = fetch_prev_ohlcv_single(code)
        if ohlcv:
            result[code] = ohlcv
            ok_cnt += 1
        else:
            logger.warning(f"[{code}] OHLCV 수집 실패 ({i}/{len(codes)})")

        if i % 20 == 0:
            logger.info(f"  진행: {i}/{len(codes)} ({ok_cnt}건 성공)")

        time.sleep(delay_sec)

    # 기존 캐시와 병합 (같은 날이면 덮어쓰지 않고 추가)
    existing = _load_cache_file()
    if existing.get("date") == today:
        existing["stocks"].update(result)
        merged = existing["stocks"]
    else:
        merged = result

    payload = {"date": today, "stocks": merged}

    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        logger.info(f"✅ ohlcv_prev.json 저장 완료: {len(merged)}건")
    except Exception as e:
        logger.error(f"ohlcv_prev.json 저장 오류: {e}")

    # 메모리 캐시 갱신
    _cache      = merged
    _cache_date = today

    return ok_cnt


# ══════════════════════════════════════════
# 캐시 파일 로드
# ══════════════════════════════════════════

def _load_cache_file() -> dict:
    """JSON 파일 로드. 없거나 오류 시 빈 dict 반환."""
    try:
        if not os.path.exists(_CACHE_PATH):
            return {"date": "", "stocks": {}}
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"ohlcv_prev.json 로드 오류: {e}")
        return {"date": "", "stocks": {}}


def load_ohlcv_cache() -> int:
    """
    프로그램 시작 시 캐시 파일을 메모리에 로드.
    오늘 날짜가 아니면 빈 캐시로 초기화.

    Returns:
        로드된 종목 수

    사용처:
        main.py — get_access_token() 직후 1회 호출
    """
    global _cache, _cache_date

    data  = _load_cache_file()
    today = date.today().isoformat()

    if data.get("date") != today:
        logger.info("ohlcv_prev.json 날짜 불일치 → 빈 캐시로 초기화 (장전 수집 대기)")
        _cache      = {}
        _cache_date = today
        return 0

    _cache      = data.get("stocks", {})
    _cache_date = today
    logger.info(f"ohlcv_prev.json 로드 완료: {len(_cache)}건")
    return len(_cache)


# ══════════════════════════════════════════
# 캐시 조회 (전략에서 사용)
# ══════════════════════════════════════════

def get_prev_ohlcv(code: str) -> dict | None:
    """
    캐시에서 전일 OHLCV 반환.
    캐시 미스 시 API 실시간 조회 후 캐시에 추가.

    Returns:
        {"open", "high", "low", "close", "volume", "trade_amount"} or None

    사용 예:
        from api.ohlcv import get_prev_ohlcv
        prev = get_prev_ohlcv("005930")
        prev_high = prev["high"]          # 전일 고가
        prev_trade = prev["trade_amount"] # 전일 거래대금(원)
    """
    global _cache

    # 메모리 캐시 히트
    if code in _cache:
        return _cache[code]

    # 캐시 미스 → 실시간 조회
    logger.debug(f"[{code}] OHLCV 캐시 미스 → 실시간 조회")
    ohlcv = fetch_prev_ohlcv_single(code)
    if ohlcv:
        _cache[code] = ohlcv
        # 파일에도 즉시 반영
        try:
            data = _load_cache_file()
            data.setdefault("stocks", {})[code] = ohlcv
            data["date"] = date.today().isoformat()
            _ensure_data_dir()
            with open(_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[{code}] ohlcv 캐시 파일 저장 실패: {e}")
    return ohlcv


def get_prev_high(code: str) -> int:
    """전일 고가 반환. 없으면 0."""
    ohlcv = get_prev_ohlcv(code)
    return ohlcv["high"] if ohlcv else 0


def get_prev_trade_amount(code: str) -> int:
    """전일 거래대금(원) 반환. 없으면 0."""
    ohlcv = get_prev_ohlcv(code)
    return ohlcv["trade_amount"] if ohlcv else 0


def get_prev_volume(code: str) -> int:
    """전일 거래량 반환. 없으면 0."""
    ohlcv = get_prev_ohlcv(code)
    return ohlcv["volume"] if ohlcv else 0


def get_atr(code: str, period: int = 14) -> float:
    """
    전일 OHLCV 기반 단순 ATR 추정값 반환.
    일봉 캐시가 1개뿐이라 다중 캔들 ATR 계산 불가 →
    전일 (고가 - 저가) 를 ATR 근사값으로 사용.
    캐시 없으면 현재가의 2% 를 기본값으로 반환.
    """
    ohlcv = get_prev_ohlcv(code)
    if ohlcv:
        high = ohlcv.get("high", 0)
        low  = ohlcv.get("low",  0)
        if high > 0 and low > 0 and high > low:
            return float(high - low)
    # 캐시 없을 때: 현재가 조회 불가 → 0 반환 (호출부에서 fallback 처리)
    return 0.0
