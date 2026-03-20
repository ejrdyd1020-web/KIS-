"""
swing/swing_position_manager.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
스윙 포지션 이월 관리 모듈
- positions_swing.json CRUD
- 익일 재시작 시 KIS 실제 잔고와 대사(reconcile)
- 갭 하락 / 목표가 도달 / 보유일 초과 청산 조건 판단
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import os
import logging
import requests
from datetime import datetime, date

from auth import get_headers, get_base_url

logger = logging.getLogger(__name__)

_BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_POS_FILE  = os.path.join(_BASE_DIR, 'data', 'positions_swing.json')


# ── CRUD ────────────────────────────────────────────────────────
def load_positions() -> dict:
    """전체 스윙 포지션 불러오기 {symbol: {...}}"""
    if not os.path.exists(_POS_FILE):
        return {}
    try:
        with open(_POS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[PosManager] 포지션 파일 읽기 실패: {e}")
        return {}


def save_positions(positions: dict):
    """전체 포지션 저장"""
    os.makedirs(os.path.dirname(_POS_FILE), exist_ok=True)
    with open(_POS_FILE, 'w', encoding='utf-8') as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def add_position(symbol: str, strategy: str, entry_price: float,
                 quantity: int, target_pct: float, stop_pct: float,
                 max_hold_days: int = 10):
    """
    신규 스윙 포지션 추가
    
    Args:
        symbol       : 종목코드
        strategy     : MOMENTUM | REVERSAL | TREND_FOLLOW
        entry_price  : 진입 단가
        quantity     : 매수 수량
        target_pct   : 목표 수익률 (예: 0.09 = +9%)
        stop_pct     : 손절률 (예: 0.04 = -4%)
        max_hold_days: 최대 보유일
    """
    positions = load_positions()
    target_price = round(entry_price * (1 + target_pct))
    stop_price   = round(entry_price * (1 - stop_pct))

    positions[symbol] = {
        'symbol'        : symbol,
        'strategy'      : strategy,
        'entry_price'   : entry_price,
        'entry_date'    : date.today().strftime('%Y-%m-%d'),
        'quantity'      : quantity,
        'target_price'  : target_price,
        'stop_loss'     : stop_price,
        'max_hold_days' : max_hold_days,
        'trailing_stop' : None,   # 고점 갱신 시 업데이트
        'highest_close' : entry_price,
        'created_at'    : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    save_positions(positions)
    logger.info(
        f"[PosManager] 포지션 추가 {symbol}({strategy}) | "
        f"진입:{entry_price:,} 목표:{target_price:,} 손절:{stop_price:,} "
        f"수량:{quantity}주"
    )


def remove_position(symbol: str):
    """포지션 제거 (청산 완료 후 호출)"""
    positions = load_positions()
    if symbol in positions:
        del positions[symbol]
        save_positions(positions)
        logger.info(f"[PosManager] 포지션 제거: {symbol}")


def update_trailing_stop(symbol: str, current_close: float, trail_pct: float = 0.05):
    """
    트레일링 스톱 업데이트
    - 고점 갱신 시 trailing_stop = 고점 × (1 - trail_pct) 로 상향
    """
    positions = load_positions()
    pos = positions.get(symbol)
    if not pos:
        return

    highest = pos.get('highest_close', pos['entry_price'])
    if current_close > highest:
        new_trail = round(current_close * (1 - trail_pct))
        positions[symbol]['highest_close'] = current_close
        positions[symbol]['trailing_stop'] = new_trail
        save_positions(positions)
        logger.debug(
            f"[PosManager] 트레일링 스톱 상향 {symbol}: "
            f"고점 {highest:,} → {current_close:,} | "
            f"스톱 → {new_trail:,}"
        )


# ── 청산 조건 판단 ──────────────────────────────────────────────
def check_exit_condition(symbol: str, current_price: float) -> str | None:
    """
    청산 조건 체크
    Returns:
        str | None: 청산 사유 or None(유지)
        - 'TARGET'        : 목표가 도달
        - 'STOP_LOSS'     : 손절가 도달
        - 'TRAILING_STOP' : 트레일링 스톱 발동
        - 'MAX_HOLD'      : 최대 보유일 초과
        - 'GAP_DOWN'      : 갭 하락 (시초가 손절가 하회)
    """
    positions = load_positions()
    pos = positions.get(symbol)
    if not pos:
        return None

    # 목표가 도달
    if current_price >= pos['target_price']:
        return 'TARGET'

    # 손절가 도달
    if current_price <= pos['stop_loss']:
        return 'STOP_LOSS'

    # 트레일링 스톱 발동
    if pos['trailing_stop'] and current_price <= pos['trailing_stop']:
        return 'TRAILING_STOP'

    # 최대 보유일 초과
    entry_date = datetime.strptime(pos['entry_date'], '%Y-%m-%d').date()
    hold_days  = (date.today() - entry_date).days
    if hold_days >= pos['max_hold_days']:
        return 'MAX_HOLD'

    return None


# ── KIS 실제 잔고 대사 ──────────────────────────────────────────
def reconcile_with_kis() -> dict:
    """
    장 시작 전 positions_swing.json ↔ KIS 실제 잔고 대사
    - JSON에 있지만 KIS에 없는 포지션 → 이미 청산된 것으로 제거
    - KIS에 있지만 JSON에 없는 스윙 포지션 → 경고 로그 출력
    
    Returns:
        dict: {'removed': [...], 'orphaned': [...]}
    """
    logger.info("[PosManager] KIS 잔고 대사 시작")
    positions = load_positions()
    if not positions:
        return {'removed': [], 'orphaned': []}

    # KIS 실제 잔고 조회
    kis_holdings = _get_kis_balance()
    if kis_holdings is None:
        logger.warning("[PosManager] KIS 잔고 조회 실패 → 대사 스킵")
        return {'removed': [], 'orphaned': []}

    kis_symbols = set(kis_holdings.keys())
    json_symbols = set(positions.keys())

    removed = []
    orphaned = []

    # JSON에 있지만 KIS에 없는 포지션 제거
    for symbol in json_symbols - kis_symbols:
        logger.warning(
            f"[PosManager] 대사 불일치: {symbol} → "
            f"JSON에는 있지만 KIS에 없음 → 포지션 제거"
        )
        remove_position(symbol)
        removed.append(symbol)

    # KIS에 있지만 JSON에 없는 종목 경고 (수동 확인 필요)
    for symbol in kis_symbols - json_symbols:
        name = kis_holdings[symbol].get('name', '')
        qty  = kis_holdings[symbol].get('quantity', 0)
        logger.warning(
            f"[PosManager] 미등록 보유 종목: {name}({symbol}) {qty}주 → "
            f"수동 확인 필요"
        )
        orphaned.append(symbol)

    logger.info(
        f"[PosManager] 대사 완료 | "
        f"제거:{len(removed)} 미등록:{len(orphaned)}"
    )
    return {'removed': removed, 'orphaned': orphaned}


def _get_kis_balance() -> dict | None:
    """
    KIS 주식 잔고 조회
    Returns:
        dict: {symbol: {'name': ..., 'quantity': ..., 'avg_price': ...}}
    """
    url = f"{get_base_url()}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = get_headers("TTTC8434R")
    params = {
        "CANO"           : os.getenv('KIS_ACCOUNT_NO', ''),
        "ACNT_PRDT_CD"   : "01",
        "AFHR_FLPR_YN"   : "N",
        "OFL_YN"         : "N",
        "INQR_DVSN"      : "01",
        "UNPR_DVSN"      : "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN"      : "00",
        "CTX_AREA_FK100" : "",
        "CTX_AREA_NK100" : "",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=8)
        data = resp.json()
        if data.get('rt_cd') != '0':
            logger.warning(f"[PosManager] 잔고 조회 오류: {data.get('msg1')}")
            return None
        result = {}
        for item in data.get('output1', []):
            qty = int(item.get('hldg_qty', 0) or 0)
            if qty > 0:
                symbol = item.get('pdno', '')
                result[symbol] = {
                    'name'     : item.get('prdt_name', ''),
                    'quantity' : qty,
                    'avg_price': float(item.get('pchs_avg_pric', 0) or 0),
                }
        return result
    except Exception as e:
        logger.error(f"[PosManager] 잔고 조회 예외: {e}")
        return None


def get_summary() -> dict:
    """포지션 현황 요약 (대시보드용)"""
    positions = load_positions()
    total_invested = sum(
        p['entry_price'] * p['quantity'] for p in positions.values()
    )
    return {
        'count'         : len(positions),
        'total_invested': total_invested,
        'symbols'       : [
            {
                'symbol'     : p['symbol'],
                'strategy'   : p['strategy'],
                'entry_price': p['entry_price'],
                'entry_date' : p['entry_date'],
                'quantity'   : p['quantity'],
                'target'     : p['target_price'],
                'stop'       : p['stop_loss'],
            }
            for p in positions.values()
        ]
    }
