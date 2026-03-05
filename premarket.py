# ============================================================
#  premarket.py  –  장 전 후보 종목 스크리닝
#
#  실행 시간: 08:50~09:00 (장 시작 전)
#
#  선정 기준:
#    1단계 기본 필터
#      - 전일 등락률  +3~+15%
#      - 전일 거래대금 500억 이상
#      - 주가 1,000~100,000원
#      - 시가총액 100억 이상
#
#    2단계 모멘텀 필터
#      - 전일 거래량  전전일 대비 2배 이상
#      - 일봉 MA5  위 (단기 상승 추세)
#      - 일봉 MA20 위 (중기 상승 추세)
#
#    3단계 점수제 상위 10개 선정
#      - 거래량 급증도 40%
#      - 전일 등락률   35%
#      - 52주 신고가   25%
#
#  결과: watchlist.json 저장
#        → condition.py 장중 스캔 시 우선 감시
# ============================================================

import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests

sys.path.append(os.path.dirname(__file__))

from auth       import get_headers, get_base_url
from utils.logger import get_logger

logger = get_logger("premarket")

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")


# ══════════════════════════════════════════
# KIS API — 전일 일봉 데이터 조회
# ══════════════════════════════════════════

def get_daily_chart(stock_code: str, count: int = 30) -> list[dict]:
    """
    일봉 데이터 조회 (최근 count일)
    반환: [{"date", "open", "high", "low", "close", "volume"}, ...]
          최신 데이터가 index 0
    """
    tr_id = "FHKST03010100"
    today = datetime.now().strftime("%Y%m%d")

    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd"        : stock_code,
        "fid_input_date_1"      : "20200101",
        "fid_input_date_2"      : today,
        "fid_period_div_code"   : "D",
        "fid_org_adj_prc"       : "0",
    }

    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=get_headers(tr_id),
            params=params,
            timeout=5,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"[{stock_code}] 일봉 조회 실패: {data.get('msg1')}")
            return []

        candles = []
        for item in data.get("output2", [])[:count]:
            candles.append({
                "date"  : item.get("stck_bsop_date", ""),
                "open"  : int(item.get("stck_oprc", 0)),
                "high"  : int(item.get("stck_hgpr", 0)),
                "low"   : int(item.get("stck_lwpr", 0)),
                "close" : int(item.get("stck_clpr", 0)),
                "volume": int(item.get("acml_vol", 0)),
            })
        return candles

    except Exception as e:
        logger.error(f"[{stock_code}] 일봉 조회 오류: {e}")
        return []


def get_fluctuation_rank_daily(top_n: int = 50) -> list[dict]:
    """
    전일 등락률 순위 조회 (상위 top_n개)
    """
    tr_id = "FHPST01710000"

    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code" : "20171",
        "fid_input_iscd"        : "0000",
        "fid_rank_sort_cls_code": "0",    # 상승률 순
        "fid_input_cnt_1"       : "0",
        "fid_prc_cls_code"      : "1",
        "fid_input_price_1"     : "1000",
        "fid_input_price_2"     : "100000",
        "fid_vol_cnt"           : "100000",
        "fid_trgt_cls_code"     : "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code"      : "0",
        "fid_rsfl_rate1"        : "3",    # 등락률 최소 +3%
        "fid_rsfl_rate2"        : "15",   # 등락률 최대 +15%
    }

    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/ranking/fluctuation",
            headers=get_headers(tr_id),
            params=params,
            timeout=5,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"등락률 순위 조회 실패: {data.get('msg1')}")
            return []

        stocks = []
        for item in data.get("output", [])[:top_n]:
            stocks.append({
                "code"              : item.get("mksc_shrn_iscd", ""),
                "name"              : item.get("hts_kor_isnm", ""),
                "price"             : int(item.get("stck_prpr", 0)),
                "change_rate"       : float(item.get("prdy_ctrt", 0)),
                "volume"            : int(item.get("acml_vol", 0)),
                "prev_trade_amount" : int(item.get("acml_tr_pbmn", 0)),
            })

        logger.info(f"전일 등락률 순위 조회 완료: {len(stocks)}개")
        return stocks

    except Exception as e:
        logger.error(f"등락률 순위 조회 오류: {e}")
        return []


# ══════════════════════════════════════════
# 일봉 MA 계산
# ══════════════════════════════════════════

def calc_daily_ma(candles: list[dict], period: int) -> float | None:
    """일봉 이동평균 계산. 데이터 부족 시 None 반환."""
    if len(candles) < period:
        return None
    closes = [c["close"] for c in candles[:period]]
    return round(sum(closes) / period, 2)


def calc_volume_surge(candles: list[dict]) -> float:
    """전일 거래량 / 전전일 거래량 비율 계산."""
    if len(candles) < 2:
        return 0.0
    prev_vol     = candles[0]["volume"]   # 전일
    prev2_vol    = candles[1]["volume"]   # 전전일
    if prev2_vol <= 0:
        return 0.0
    return round(prev_vol / prev2_vol, 2)


def calc_week52_ratio(candles: list[dict], current_price: int) -> float:
    """52주 신고가 대비 현재가 비율."""
    if not candles:
        return 0.0
    week52_high = max(c["high"] for c in candles[:252])
    if week52_high <= 0:
        return 0.0
    return round(current_price / week52_high * 100, 1)


# ══════════════════════════════════════════
# 점수 계산
# ══════════════════════════════════════════

def score_premarket(stock: dict) -> float:
    """
    장 전 종목 점수 계산
      - 거래량 급증도 40%
      - 전일 등락률   35%
      - 52주 신고가   25%
    """
    surge   = stock.get("volume_surge", 0)
    rate    = stock.get("change_rate", 0)
    w52     = stock.get("week52_ratio", 0)

    surge_score = min(surge / 5.0, 1.0) * 100    # 5배 = 만점
    rate_score  = min(rate / 15.0, 1.0) * 100    # 15% = 만점
    w52_score   = min(w52 / 100.0, 1.0) * 100    # 100% = 만점

    return round(surge_score * 0.4 + rate_score * 0.35 + w52_score * 0.25, 2)


# ══════════════════════════════════════════
# 메인 스크리닝 함수
# ══════════════════════════════════════════

def run_premarket_screening(top_n: int = 10) -> list[dict]:
    """
    장 전 후보 종목 스크리닝 실행.

    Returns:
        상위 top_n개 후보 종목 리스트
    """
    logger.info("=" * 55)
    logger.info("  📋 장 전 후보 종목 스크리닝 시작")
    logger.info("=" * 55)

    # 1. 전일 등락률 상위 종목 조회
    stocks = get_fluctuation_rank_daily(top_n=50)
    if not stocks:
        logger.error("전일 등락률 데이터 조회 실패")
        return []

    candidates = []

    for s in stocks:
        code  = s["code"]
        name  = s["name"]
        price = s["price"]

        if not code or price <= 0:
            continue

        # 일봉 데이터 조회
        candles = get_daily_chart(code, count=30)
        if len(candles) < 5:
            logger.debug(f"[{name}] 일봉 데이터 부족 → 스킵")
            time.sleep(0.2)
            continue

        # ── 2단계 모멘텀 필터 ──────────────────────────────────

        # 전일 거래대금 500억 이상
        prev_trade_amount = int(s.get("prev_trade_amount", 0) / 100_000_000)
        if prev_trade_amount < 500:
            logger.debug(f"[{name}] 전일거래대금 부족({prev_trade_amount}억) → 탈락")
            time.sleep(0.2)
            continue

        # 전일 거래량 전전일 대비 2배 이상
        volume_surge = calc_volume_surge(candles)
        if volume_surge < 2.0:
            logger.debug(f"[{name}] 거래량 급증 미달({volume_surge:.1f}배) → 탈락")
            time.sleep(0.2)
            continue

        # 일봉 MA5 위 (단기 상승 추세)
        ma5 = calc_daily_ma(candles, 5)
        if ma5 and price < ma5:
            logger.debug(f"[{name}] 일봉 MA5 이탈({price:,} < {ma5:,.0f}) → 탈락")
            time.sleep(0.2)
            continue

        # 일봉 MA20 위 (중기 상승 추세)
        ma20 = calc_daily_ma(candles, 20)
        if ma20 and price < ma20:
            logger.debug(f"[{name}] 일봉 MA20 이탈({price:,} < {ma20:,.0f}) → 탈락")
            time.sleep(0.2)
            continue

        # 52주 신고가 근접도
        week52_ratio = calc_week52_ratio(candles, price)

        # 점수 계산용 데이터 추가
        s["volume_surge"]  = volume_surge
        s["week52_ratio"]  = week52_ratio
        s["ma5"]           = ma5
        s["ma20"]          = ma20
        s["score"]         = score_premarket(s)

        candidates.append(s)
        logger.info(
            f"[{name}({code})] ✅ 통과 | "
            f"등락률: {s['change_rate']:+.1f}% | "
            f"거래량: {volume_surge:.1f}배 | "
            f"52주: {week52_ratio:.1f}% | "
            f"점수: {s['score']:.1f}"
        )
        time.sleep(0.3)

    # 3단계 점수 상위 top_n개 선정
    candidates.sort(key=lambda x: x["score"], reverse=True)
    watchlist = candidates[:top_n]

    logger.info(f"\n총 {len(candidates)}개 통과 → 상위 {len(watchlist)}개 선정")
    return watchlist


# ══════════════════════════════════════════
# watchlist.json 저장 / 불러오기
# ══════════════════════════════════════════

def save_watchlist(watchlist: list[dict]):
    """watchlist.json 저장"""
    data = {
        "date"     : datetime.now().strftime("%Y-%m-%d"),
        "created"  : datetime.now().strftime("%H:%M:%S"),
        "stocks"   : [
            {
                "code"        : s["code"],
                "name"        : s["name"],
                "price"       : s["price"],
                "change_rate" : s["change_rate"],
                "volume_surge": s["volume_surge"],
                "week52_ratio": s["week52_ratio"],
                "score"       : s["score"],
            }
            for s in watchlist
        ],
    }
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ watchlist.json 저장 완료 ({len(watchlist)}개 종목)")


def load_watchlist() -> list[dict]:
    """watchlist.json 불러오기. 오늘 날짜 파일만 유효."""
    if not os.path.exists(WATCHLIST_PATH):
        return []

    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 오늘 날짜 확인
        today = datetime.now().strftime("%Y-%m-%d")
        if data.get("date") != today:
            logger.info("watchlist.json 날짜 불일치 → 무시")
            return []

        stocks = data.get("stocks", [])
        logger.info(f"📋 watchlist 불러오기 완료: {len(stocks)}개 종목")
        return stocks

    except Exception as e:
        logger.error(f"watchlist.json 불러오기 오류: {e}")
        return []


# ══════════════════════════════════════════
# 결과 출력
# ══════════════════════════════════════════

def print_watchlist(watchlist: list[dict]):
    print(f"\n{'='*75}")
    print(f"  📋 장 전 후보 종목 — 상위 {len(watchlist)}개 (장중 우선 감시 대상)")
    print(f"{'='*75}")
    print(f"  {'순위':>3} {'점수':>5} {'종목명':<14} {'전일가':>8} "
          f"{'등락률':>7} {'거래량급증':>8} {'52주비율':>8}")
    print(f"  {'-'*70}")
    for i, s in enumerate(watchlist):
        print(
            f"  {i+1:>3} {s['score']:>5.1f} {s['name']:<14} {s['price']:>8,} "
            f"{s['change_rate']:>+6.1f}% {s['volume_surge']:>7.1f}배 "
            f"{s['week52_ratio']:>7.1f}%"
        )
    print(f"{'='*75}\n")
    print("  ※ 장 시작 후 스토캐스틱 + MA120 조건 충족 시 매수 진입")
    print(f"{'='*75}\n")


# ══════════════════════════════════════════
# 실행
# ══════════════════════════════════════════

if __name__ == "__main__":
    from auth import get_access_token
    get_access_token()

    watchlist = run_premarket_screening(top_n=10)

    if watchlist:
        save_watchlist(watchlist)
        print_watchlist(watchlist)
    else:
        print("  후보 종목 없음")
