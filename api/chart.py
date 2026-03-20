"""
api/chart.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KIS API 차트(캔들) 데이터 조회 모듈
- 기존: get_minute_candles() - 분봉 (단타용)
- 신규: get_daily_ohlcv()   - 일봉 (스윙용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
import logging
import time
from datetime import datetime, timedelta
from auth import get_headers, get_base_url

logger = logging.getLogger(__name__)

# API 호출 간격 (초) - 단타보다 여유있게
_API_INTERVAL = 0.22


# ── 기존 분봉 조회 (단타용, 변경 없음) ────────────────────────
def get_minute_candles(symbol: str, count: int = 125) -> list[dict] | None:
    """
    분봉 데이터 조회 (단타 BREAKOUT/REVERSION 전략용)
    
    Args:
        symbol: 종목코드
        count : 요청 캔들 수 (최대 125)
    Returns:
        list of dict: [{time, open, high, low, close, volume}, ...] 최신→과거 순
    """
    url = f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    headers = get_headers("FHKST03010200")
    params = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_HOUR_1": datetime.now().strftime("%H%M%S"),
        "FID_PW_DATA_INCU_YN": "Y",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        data = resp.json()
        if data.get('rt_cd') != '0':
            logger.warning(f"[chart] 분봉 오류 {symbol}: {data.get('msg1')}")
            return None
        candles = data.get('output2', [])[:count]
        return [
            {
                'time' : c.get('stck_cntg_hour', ''),
                'open' : int(c.get('stck_oprc', 0)),
                'high' : int(c.get('stck_hgpr', 0)),
                'low'  : int(c.get('stck_lwpr', 0)),
                'close': int(c.get('stck_prpr', 0)),
                'volume': int(c.get('cntg_vol', 0)),
            }
            for c in candles if c.get('stck_prpr')
        ]
    except Exception as e:
        logger.error(f"[chart] 분봉 조회 예외 {symbol}: {e}")
        return None


# ── 신규: 일봉 OHLCV 조회 (스윙용) ──────────────────────────
def get_daily_ohlcv(symbol: str, count: int = 120) -> list[dict] | None:
    """
    일봉 OHLCV 데이터 조회 (스윙 전략용)
    KIS API: 주식일자별시세 (FHKST01010400)
    
    Args:
        symbol: 종목코드 (예: "005930")
        count : 조회 일수 (최대 100일/1회 호출, 120일은 2회 호출로 처리)
    Returns:
        list of dict: [{date, open, high, low, close, volume, amount}, ...]
                      최신→과거 순 정렬
    """
    all_candles = []
    # KIS 일봉 API는 1회 최대 100건 → 120일 요청 시 2회 호출
    end_date = datetime.now()

    for i in range(2):  # 최대 2회 호출
        if len(all_candles) >= count:
            break

        fetch_end = end_date - timedelta(days=i * 100)
        fetch_start = fetch_end - timedelta(days=100)

        candles = _fetch_daily_chunk(
            symbol,
            fetch_start.strftime("%Y%m%d"),
            fetch_end.strftime("%Y%m%d"),
        )
        if candles:
            all_candles.extend(candles)
        time.sleep(_API_INTERVAL)

    if not all_candles:
        return None

    # 중복 제거 + 날짜 기준 정렬 (최신→과거)
    seen = set()
    result = []
    for c in all_candles:
        if c['date'] not in seen:
            seen.add(c['date'])
            result.append(c)

    result.sort(key=lambda x: x['date'], reverse=True)
    return result[:count]


def _fetch_daily_chunk(symbol: str, start_date: str, end_date: str) -> list[dict] | None:
    """
    일봉 단일 구간 조회 (내부 함수)
    
    Args:
        symbol    : 종목코드
        start_date: 시작일 YYYYMMDD
        end_date  : 종료일 YYYYMMDD
    """
    url = f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = get_headers("FHKST03010100")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD"        : symbol,
        "FID_INPUT_DATE_1"      : start_date,
        "FID_INPUT_DATE_2"      : end_date,
        "FID_PERIOD_DIV_CODE"   : "D",   # D=일봉, W=주봉, M=월봉
        "FID_ORG_ADJ_PRC"       : "0",   # 0=수정주가
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=8)
        data = resp.json()
        if data.get('rt_cd') != '0':
            logger.warning(f"[chart] 일봉 오류 {symbol}: {data.get('msg1')}")
            return None

        candles = data.get('output2', [])
        return [
            {
                'date'  : c.get('stck_bsop_date', ''),   # YYYYMMDD
                'open'  : int(c.get('stck_oprc', 0) or 0),
                'high'  : int(c.get('stck_hgpr', 0) or 0),
                'low'   : int(c.get('stck_lwpr', 0) or 0),
                'close' : int(c.get('stck_clpr', 0) or 0),
                'volume': int(c.get('acml_vol', 0) or 0),
                'amount': int(c.get('acml_tr_pbmn', 0) or 0),  # 거래대금
            }
            for c in candles
            if c.get('stck_bsop_date') and int(c.get('stck_clpr', 0) or 0) > 0
        ]
    except Exception as e:
        logger.error(f"[chart] 일봉 청크 조회 예외 {symbol} ({start_date}~{end_date}): {e}")
        return None


# ── 일봉 기반 보조지표 계산 (스윙 전략 공통 유틸) ────────────────
def calc_indicators(candles: list[dict]) -> dict | None:
    """
    일봉 데이터로 스윙 전략에 필요한 보조지표 일괄 계산
    
    Args:
        candles: get_daily_ohlcv() 반환값 (최신→과거 순)
    Returns:
        dict: 각종 지표값
    """
    if not candles or len(candles) < 20:
        return None

    closes  = [c['close']  for c in candles]
    volumes = [c['volume'] for c in candles]
    highs   = [c['high']   for c in candles]
    lows    = [c['low']    for c in candles]

    def ma(data, n): 
        return sum(data[:n]) / n if len(data) >= n else None

    # ── 이동평균 ─────────────────────────────────
    ma5   = ma(closes, 5)
    ma20  = ma(closes, 20)
    ma60  = ma(closes, 60)
    ma120 = ma(closes, 120) if len(closes) >= 120 else None

    # ── MA5 기울기 (최근 3일 평균 기울기) ──────────
    ma5_slope = None
    if len(closes) >= 7:
        ma5_prev = ma(closes[2:], 5)
        ma5_curr = ma(closes, 5)
        if ma5_prev and ma5_curr:
            ma5_slope = (ma5_curr - ma5_prev) / ma5_prev * 100  # %

    # ── 골든크로스 / 데드크로스 ─────────────────────
    golden_cross = dead_cross = False
    if len(closes) >= 21:
        ma5_prev2  = ma(closes[1:], 5)
        ma20_prev2 = ma(closes[1:], 20)
        if ma5_prev2 and ma20_prev2 and ma5 and ma20:
            if ma5_prev2 <= ma20_prev2 and ma5 > ma20:
                golden_cross = True
            elif ma5_prev2 >= ma20_prev2 and ma5 < ma20:
                dead_cross = True

    # ── RSI(14) ──────────────────────────────────
    rsi = _calc_rsi(closes, 14)

    # ── 볼린저 밴드(20,2) ─────────────────────────
    bb_upper = bb_lower = bb_mid = None
    if len(closes) >= 20:
        bb_mid   = ma20
        std20    = (sum((c - bb_mid) ** 2 for c in closes[:20]) / 20) ** 0.5
        bb_upper = bb_mid + 2 * std20
        bb_lower = bb_mid - 2 * std20

    # ── MACD(12,26,9) ────────────────────────────
    macd_line, macd_signal, macd_hist = _calc_macd(closes)

    # ── 거래량 지표 ───────────────────────────────
    vol_ma20 = ma(volumes, 20)
    vol_ratio = (volumes[0] / vol_ma20) if vol_ma20 and vol_ma20 > 0 else 0

    # ── 52주 신고가 대비 위치 ─────────────────────
    high_52w = max(highs[:min(len(highs), 252)])
    current  = closes[0]
    dist_from_52w_high = (current / high_52w - 1) * 100  # % (음수면 고점 대비 하락)

    # ── 연속 음봉/양봉 카운트 ────────────────────
    consec_red = consec_green = 0
    for c in candles:
        if c['close'] < c['open']:
            if consec_green > 0: break
            consec_red += 1
        elif c['close'] > c['open']:
            if consec_red > 0: break
            consec_green += 1
        else:
            break

    return {
        'close'             : current,
        'ma5'               : ma5,
        'ma20'              : ma20,
        'ma60'              : ma60,
        'ma120'             : ma120,
        'ma5_slope'         : ma5_slope,
        'golden_cross'      : golden_cross,
        'dead_cross'        : dead_cross,
        'rsi'               : rsi,
        'bb_upper'          : bb_upper,
        'bb_mid'            : bb_mid,
        'bb_lower'          : bb_lower,
        'macd_line'         : macd_line,
        'macd_signal'       : macd_signal,
        'macd_hist'         : macd_hist,
        'vol_ma20'          : vol_ma20,
        'vol_ratio'         : vol_ratio,
        'high_52w'          : high_52w,
        'dist_from_52w_high': dist_from_52w_high,
        'consec_red'        : consec_red,
        'consec_green'      : consec_green,
    }


def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI 계산 (Wilder 방식)"""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(period):
        diff = closes[i] - closes[i + 1]  # 최신→과거 순이므로 부호 반전
        if diff > 0:
            gains.append(diff); losses.append(0)
        else:
            gains.append(0); losses.append(-diff)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _calc_macd(closes: list[float], fast=12, slow=26, signal=9):
    """MACD 계산 (EMA 기반)"""
    def ema(data, n):
        if len(data) < n:
            return None
        k = 2 / (n + 1)
        val = sum(data[-n:]) / n  # 초기값 SMA
        for price in reversed(data[:-n]):
            val = price * k + val * (1 - k)
        return val

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    if not ema_fast or not ema_slow:
        return None, None, None
    macd_line = ema_fast - ema_slow
    # signal은 macd_line 시계열이 필요하지만 단순화: 당일값 반환
    return round(macd_line, 2), None, None
