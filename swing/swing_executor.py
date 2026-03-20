"""
swing/swing_executor.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
스윙 매수/매도 실행 모듈
- 지정가/시장가 주문
- 포지션 추가, 청산 (전량/부분)
- 청산 사유 로깅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import logging
import requests

from auth import get_headers, get_base_url
from shared.symbol_lock import acquire as lock_acquire, release as lock_release
from swing.swing_position_manager import (
    add_position, remove_position, update_trailing_stop,
    check_exit_condition, load_positions
)
from swing.swing_risk import (
    can_open_position, calc_quantity, calc_target_stop,
    get_swing_budget, get_position_budget
)

logger = logging.getLogger(__name__)

# KIS 계좌번호 (환경변수)
_ACCOUNT = os.getenv('KIS_ACCOUNT_NO', '')


def try_buy(symbol: str, name: str, strategy: str,
            score: float, total_score: float) -> bool:
    """
    스윙 매수 시도
    
    Args:
        symbol      : 종목코드
        name        : 종목명
        strategy    : MOMENTUM | REVERSAL | TREND_FOLLOW
        score       : 해당 종목 스코어
        total_score : 전체 후보 스코어 합
    Returns:
        bool: 매수 성공 여부
    """
    # 1. 포지션 개설 가능 여부 확인
    ok, reason = can_open_position(symbol)
    if not ok:
        logger.info(f"[Executor] 매수 스킵 {name}({symbol}): {reason}")
        return False

    # 2. 종목 Lock 획득
    if not lock_acquire(symbol, 'SWING'):
        logger.info(f"[Executor] 매수 스킵 {name}({symbol}): 다른 전략 점유 중")
        return False

    try:
        # 3. 현재가 조회
        current_price = _get_current_price(symbol)
        if not current_price:
            logger.warning(f"[Executor] 현재가 조회 실패: {symbol}")
            lock_release(symbol, 'SWING')
            return False

        # 4. 투자 금액 및 수량 계산
        swing_budget = get_swing_budget()
        amount       = get_position_budget(swing_budget, score, total_score)
        quantity     = calc_quantity(amount, current_price)

        if quantity <= 0:
            logger.warning(
                f"[Executor] 매수 불가 {name}({symbol}): "
                f"예산:{amount:,.0f} / 현재가:{current_price:,} → 수량 0"
            )
            lock_release(symbol, 'SWING')
            return False

        # 5. 목표가/손절 파라미터
        target_pct, stop_pct, trail_pct = calc_target_stop(current_price, strategy)

        # 6. KIS 매수 주문 (지정가: 현재가 기준)
        order_price = current_price
        success = _send_order(
            symbol=symbol,
            order_type='BUY',
            price=order_price,
            quantity=quantity,
        )

        if not success:
            lock_release(symbol, 'SWING')
            return False

        # 7. 포지션 등록
        add_position(
            symbol=symbol,
            strategy=strategy,
            entry_price=order_price,
            quantity=quantity,
            target_pct=target_pct,
            stop_pct=stop_pct,
            max_hold_days=10,
        )

        logger.info(
            f"[Executor] ✅ 매수 완료 {name}({symbol}) | "
            f"{strategy} | {order_price:,}원 × {quantity}주 = "
            f"{order_price * quantity:,.0f}원"
        )
        return True

    except Exception as e:
        logger.error(f"[Executor] 매수 예외 {symbol}: {e}")
        lock_release(symbol, 'SWING')
        return False


def check_and_exit(symbol: str, current_price: float) -> bool:
    """
    보유 포지션 청산 조건 체크 및 실행
    
    Args:
        symbol       : 종목코드
        current_price: 현재가
    Returns:
        bool: 청산 실행 여부
    """
    # 트레일링 스톱 갱신 (고점 업데이트)
    update_trailing_stop(symbol, current_price)

    # 청산 조건 확인
    reason = check_exit_condition(symbol, current_price)
    if not reason:
        return False

    positions = load_positions()
    pos = positions.get(symbol)
    if not pos:
        return False

    quantity = pos['quantity']
    entry    = pos['entry_price']
    pnl      = (current_price - entry) * quantity
    pnl_pct  = (current_price / entry - 1) * 100

    logger.info(
        f"[Executor] 청산 신호 {symbol} | 사유:{reason} | "
        f"진입:{entry:,} 현재:{current_price:,} | "
        f"손익:{pnl:+,.0f}원 ({pnl_pct:+.2f}%)"
    )

    # KIS 매도 주문
    order_type = 'SELL_MARKET' if reason in ('STOP_LOSS', 'GAP_DOWN') else 'SELL'
    success = _send_order(
        symbol=symbol,
        order_type=order_type,
        price=current_price,
        quantity=quantity,
    )

    if success:
        remove_position(symbol)
        lock_release(symbol, 'SWING')
        logger.info(
            f"[Executor] ✅ 청산 완료 {symbol} | {reason} | "
            f"실현손익:{pnl:+,.0f}원"
        )
    return success


def exit_all_positions(reason: str = 'FORCE_EXIT'):
    """
    전체 스윙 포지션 강제 청산 (긴급 대응용)
    """
    positions = load_positions()
    if not positions:
        logger.info("[Executor] 청산할 포지션 없음")
        return

    logger.warning(f"[Executor] 전체 포지션 강제 청산 시작 | 사유:{reason}")
    for symbol, pos in list(positions.items()):
        price = _get_current_price(symbol) or pos['entry_price']
        _send_order(symbol, 'SELL_MARKET', price, pos['quantity'])
        remove_position(symbol)
        lock_release(symbol, 'SWING')
        logger.info(f"[Executor] 강제 청산: {symbol}")


# ── KIS 주문 API ────────────────────────────────────────────────
def _send_order(symbol: str, order_type: str,
                price: float, quantity: int) -> bool:
    """
    KIS 주식 주문 API 호출
    
    Args:
        order_type: 'BUY' | 'SELL' | 'SELL_MARKET'
    """
    url = f"{get_base_url()}/uapi/domestic-stock/v1/trading/order-cash"

    is_buy    = order_type == 'BUY'
    is_market = order_type == 'SELL_MARKET'

    tr_id = "TTTC0802U" if is_buy else "TTTC0801U"
    headers = get_headers(tr_id)

    body = {
        "CANO"        : _ACCOUNT,
        "ACNT_PRDT_CD": "01",
        "PDNO"        : symbol,
        "ORD_DVSN"    : "01" if is_market else "00",  # 01=시장가, 00=지정가
        "ORD_QTY"     : str(quantity),
        "ORD_UNPR"    : "0" if is_market else str(int(price)),
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=5)
        data = resp.json()
        if data.get('rt_cd') != '0':
            logger.error(
                f"[Executor] 주문 실패 {symbol} {order_type}: "
                f"{data.get('msg1')}"
            )
            return False
        order_no = data.get('output', {}).get('ODNO', 'N/A')
        logger.info(
            f"[Executor] 주문 접수 {symbol} {order_type} "
            f"{quantity}주 @{price:,} | 주문번호:{order_no}"
        )
        return True
    except Exception as e:
        logger.error(f"[Executor] 주문 예외 {symbol}: {e}")
        return False


def _get_current_price(symbol: str) -> int | None:
    """현재가 단순 조회"""
    url = f"{get_base_url()}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = get_headers("FHKST01010100")
    params  = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        data = resp.json()
        if data.get('rt_cd') == '0':
            return int(data['output'].get('stck_prpr', 0) or 0)
    except Exception:
        pass
    return None
