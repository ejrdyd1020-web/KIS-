"""
swing/swing_scanner.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
스윙매매 후보 종목 스캔 모듈
- 매일 08:50 premarket.py에서 호출
- 일봉 기반 3가지 전략(MOMENTUM / REVERSAL / TREND_FOLLOW) 후보 선별
- ETF/레버리지 이중 필터(키워드 + 상품구분코드)
- 결과: data/watchlist_swing.json 저장
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import os
import time
import logging
import requests
from datetime import datetime

from auth import get_headers, get_base_url
from api.chart import get_daily_ohlcv, calc_indicators

logger = logging.getLogger(__name__)

# ── 경로 설정 ──────────────────────────────────────────────────
_BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUTPUT     = os.path.join(_BASE_DIR, 'data', 'watchlist_swing.json')

# ── ETF/레버리지 제외 키워드 (기존 단타 15개 + 스윙 추가) ──────
_ETF_KEYWORDS = [
    'KODEX', 'TIGER', 'KBSTAR', 'ARIRANG', 'HANARO', 'KOSEF',
    'SKAI', 'SOL', 'ACE', 'TIMEFOLIO', 'TRUSTON',
    '레버리지', '인버스', '2X', '3X', 'ETN', 'ETF',
    'SHORT', 'ULTRA', '인덱스', '섹터',
]

# ── 스캔 파라미터 ──────────────────────────────────────────────
_PARAMS = {
    # 공통 필터
    'MIN_CLOSE'       : 3000,      # 최소 주가 (동전주 제외)
    'MAX_CLOSE'       : 300000,    # 최대 주가
    'MIN_VOLUME_MA20' : 50000,     # 최소 20일 평균 거래량
    'MIN_AMOUNT'      : 3_000_000_000,  # 최소 거래대금 30억

    # MOMENTUM 전략 조건
    'MOM_VOL_RATIO'   : 1.5,       # 거래량 20일 평균 대비 배수
    'MOM_52W_DIST'    : -5.0,      # 52주 신고가 대비 -5% 이내
    'MOM_RSI_MIN'     : 45,        # RSI 하한 (과매수 아닌 상승 초입)
    'MOM_RSI_MAX'     : 70,        # RSI 상한

    # REVERSAL 전략 조건
    'REV_RSI_MAX'     : 32,        # RSI 과매도 기준
    'REV_CONSEC_RED'  : 3,         # 연속 음봉 최소 일수

    # TREND_FOLLOW 전략 조건
    'TRF_MA5_SLOPE_MIN': 0.1,      # MA5 기울기 양수 최소값 (%)
    'TRF_RSI_MIN'     : 40,
    'TRF_RSI_MAX'     : 65,

    # 스캔 대상 수
    'MAX_SCAN'        : 200,       # 상위 거래대금 종목 수
    'MAX_CANDIDATES'  : 10,        # 전략별 최대 후보 수
}


# ── 메인 스캔 함수 ──────────────────────────────────────────────
def run_scan() -> dict:
    """
    전체 스윙 스캔 실행
    Returns:
        dict: {
            'MOMENTUM': [...],
            'REVERSAL': [...],
            'TREND_FOLLOW': [...],
            'scanned_at': '...',
            'total': N
        }
    """
    logger.info("[SwingScanner] 스캔 시작")
    start_time = time.time()

    # 1. 상위 거래대금 종목 목록 가져오기
    universe = _get_universe()
    if not universe:
        logger.error("[SwingScanner] 종목 유니버스 조회 실패")
        return {}

    logger.info(f"[SwingScanner] 유니버스 {len(universe)}개 종목 스캔 시작")

    momentum_list    = []
    reversal_list    = []
    trend_follow_list = []

    for i, stock in enumerate(universe[:_PARAMS['MAX_SCAN']]):
        symbol = stock['symbol']
        name   = stock['name']

        # ETF/레버리지 이중 필터
        if _is_etf(name, stock.get('product_type', '')):
            continue

        # 일봉 데이터 조회
        candles = get_daily_ohlcv(symbol, count=120)
        if not candles or len(candles) < 20:
            continue

        # 지표 계산
        ind = calc_indicators(candles)
        if not ind:
            continue

        # 공통 필터 통과 여부
        if not _pass_common_filter(ind, stock):
            continue

        # 전략별 조건 체크
        result_base = {
            'symbol'   : symbol,
            'name'     : name,
            'close'    : ind['close'],
            'rsi'      : ind['rsi'],
            'vol_ratio': round(ind['vol_ratio'], 2),
            'ma5'      : round(ind['ma5'], 0) if ind['ma5'] else None,
            'ma20'     : round(ind['ma20'], 0) if ind['ma20'] else None,
        }

        if _check_momentum(ind) and len(momentum_list) < _PARAMS['MAX_CANDIDATES']:
            momentum_list.append({
                **result_base,
                'strategy'        : 'MOMENTUM',
                'golden_cross'    : ind['golden_cross'],
                'dist_from_52w_high': round(ind['dist_from_52w_high'], 2),
            })
            logger.info(f"[SwingScanner] MOMENTUM 후보: {name}({symbol})")

        if _check_reversal(ind) and len(reversal_list) < _PARAMS['MAX_CANDIDATES']:
            reversal_list.append({
                **result_base,
                'strategy'   : 'REVERSAL',
                'consec_red' : ind['consec_red'],
                'bb_lower'   : round(ind['bb_lower'], 0) if ind['bb_lower'] else None,
            })
            logger.info(f"[SwingScanner] REVERSAL 후보: {name}({symbol})")

        if _check_trend_follow(ind) and len(trend_follow_list) < _PARAMS['MAX_CANDIDATES']:
            trend_follow_list.append({
                **result_base,
                'strategy'  : 'TREND_FOLLOW',
                'ma5_slope' : round(ind['ma5_slope'], 3) if ind['ma5_slope'] else None,
                'macd_hist' : ind['macd_hist'],
            })
            logger.info(f"[SwingScanner] TREND_FOLLOW 후보: {name}({symbol})")

        # 전략별 최대 후보 모두 채우면 조기 종료
        if (len(momentum_list)     >= _PARAMS['MAX_CANDIDATES'] and
            len(reversal_list)     >= _PARAMS['MAX_CANDIDATES'] and
            len(trend_follow_list) >= _PARAMS['MAX_CANDIDATES']):
            break

        # API 쿼터 보호
        if i % 10 == 9:
            time.sleep(0.5)

    elapsed = round(time.time() - start_time, 1)
    total   = len(momentum_list) + len(reversal_list) + len(trend_follow_list)

    result = {
        'MOMENTUM'    : momentum_list,
        'REVERSAL'    : reversal_list,
        'TREND_FOLLOW': trend_follow_list,
        'scanned_at'  : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'elapsed_sec' : elapsed,
        'total'       : total,
    }

    _save_watchlist(result)
    logger.info(
        f"[SwingScanner] 완료 | "
        f"MOM:{len(momentum_list)} REV:{len(reversal_list)} TRF:{len(trend_follow_list)} "
        f"| {elapsed}초"
    )
    return result


# ── 전략별 진입 조건 ────────────────────────────────────────────
def _check_momentum(ind: dict) -> bool:
    """
    MOMENTUM 전략 조건
    - MA5 > MA20 골든크로스
    - 거래량 20일 평균 1.5배 이상
    - 52주 신고가 5% 이내
    - RSI 45~70
    """
    if not ind['golden_cross']:
        return False
    if ind['vol_ratio'] < _PARAMS['MOM_VOL_RATIO']:
        return False
    if ind['dist_from_52w_high'] < _PARAMS['MOM_52W_DIST']:
        return False
    if not ind['rsi']:
        return False
    if not (_PARAMS['MOM_RSI_MIN'] <= ind['rsi'] <= _PARAMS['MOM_RSI_MAX']):
        return False
    return True


def _check_reversal(ind: dict) -> bool:
    """
    REVERSAL 전략 조건
    - RSI < 32 과매도
    - 연속 음봉 3일 이상
    - 현재가 볼린저 하단 근접 (하단 × 1.02 이하)
    """
    if not ind['rsi'] or ind['rsi'] > _PARAMS['REV_RSI_MAX']:
        return False
    if ind['consec_red'] < _PARAMS['REV_CONSEC_RED']:
        return False
    if ind['bb_lower'] and ind['close'] > ind['bb_lower'] * 1.02:
        return False
    return True


def _check_trend_follow(ind: dict) -> bool:
    """
    TREND_FOLLOW 전략 조건
    - MA5 기울기 양수
    - MA20 기울기 양수 (MA5 > MA20)
    - RSI 40~65 (눌림목 구간)
    - MACD 히스토그램 양전환 (있는 경우)
    """
    if not ind['ma5_slope'] or ind['ma5_slope'] < _PARAMS['TRF_MA5_SLOPE_MIN']:
        return False
    if not (ind['ma5'] and ind['ma20'] and ind['ma5'] > ind['ma20']):
        return False
    if not ind['rsi']:
        return False
    if not (_PARAMS['TRF_RSI_MIN'] <= ind['rsi'] <= _PARAMS['TRF_RSI_MAX']):
        return False
    return True


# ── 공통 필터 ───────────────────────────────────────────────────
def _pass_common_filter(ind: dict, stock: dict) -> bool:
    """주가 범위 / 거래량 / 거래대금 기본 필터"""
    close = ind['close']
    if not (_PARAMS['MIN_CLOSE'] <= close <= _PARAMS['MAX_CLOSE']):
        return False
    if not ind['vol_ma20'] or ind['vol_ma20'] < _PARAMS['MIN_VOLUME_MA20']:
        return False
    if stock.get('amount', 0) < _PARAMS['MIN_AMOUNT']:
        return False
    return True


def _is_etf(name: str, product_type: str = '') -> bool:
    """
    ETF/레버리지 이중 필터
    1) 종목명 키워드 검사
    2) KIS 상품구분코드 검사 (ETF: '02', ETN: '03')
    """
    name_upper = name.upper()
    if any(kw.upper() in name_upper for kw in _ETF_KEYWORDS):
        return True
    if product_type in ('02', '03', '04'):  # ETF, ETN, 인프라펀드
        return True
    return False


# ── 종목 유니버스 조회 ──────────────────────────────────────────
def _get_universe() -> list[dict] | None:
    """
    거래대금 상위 종목 목록 조회
    KIS API: 주식 거래대금상위 조회 (FHPST01710000)
    """
    url = f"{get_base_url()}/uapi/domestic-stock/v1/ranking/volume-power"
    headers = get_headers("FHPST01710000")
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code" : "20171",
        "fid_input_iscd"        : "0000",   # 전체
        "fid_div_cls_code"      : "0",
        "fid_blng_cls_code"     : "0",
        "fid_trgt_cls_code"     : "111111111",
        "fid_trgt_exls_cls_code": "000000",
        "fid_input_price_1"     : str(_PARAMS['MIN_CLOSE']),
        "fid_input_price_2"     : str(_PARAMS['MAX_CLOSE']),
        "fid_vol_cnt"           : str(_PARAMS['MIN_VOLUME_MA20']),
        "fid_input_date_1"      : "",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        if data.get('rt_cd') != '0':
            logger.warning(f"[SwingScanner] 유니버스 조회 오류: {data.get('msg1')}")
            return None
        return [
            {
                'symbol'      : item.get('mksc_shrn_iscd', ''),
                'name'        : item.get('hts_kor_isnm', ''),
                'close'       : int(item.get('stck_prpr', 0) or 0),
                'amount'      : int(item.get('acml_tr_pbmn', 0) or 0),
                'product_type': item.get('mrkt_cls_code', ''),
            }
            for item in data.get('output', [])
            if item.get('mksc_shrn_iscd')
        ]
    except Exception as e:
        logger.error(f"[SwingScanner] 유니버스 조회 예외: {e}")
        return None


# ── watchlist 저장 ───────────────────────────────────────────────
def _save_watchlist(data: dict):
    os.makedirs(os.path.dirname(_OUTPUT), exist_ok=True)
    with open(_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"[SwingScanner] watchlist_swing.json 저장 완료: {_OUTPUT}")


def load_watchlist() -> dict:
    """watchlist_swing.json 불러오기 (swing_main에서 사용)"""
    if not os.path.exists(_OUTPUT):
        return {}
    with open(_OUTPUT, 'r', encoding='utf-8') as f:
        return json.load(f)
