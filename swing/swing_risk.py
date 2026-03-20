"""
swing/swing_risk.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
자본 풀 분리 및 리스크 관리 모듈
- 단타(INTRADAY) / 스윙(SWING) / 예비(RESERVE) 자본 풀 분리
- 포지션당 투자 금액 계산 (스코어 비례 배분)
- 최대 동시 포지션 수 통제
- 실시간 KIS 잔고 기반 주문 가능 금액 검증
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import logging
import requests

from auth import get_headers, get_base_url
from swing.swing_position_manager import load_positions

logger = logging.getLogger(__name__)

# ── 자본 배분 비율 (config.py에서 오버라이드 가능) ──────────────
try:
    from config import (
        SWING_CAPITAL_RATIO,
        INTRADAY_CAPITAL_RATIO,
        RESERVE_RATIO,
        SWING_MAX_POSITIONS,
        SWING_MAX_SINGLE_RATIO,
    )
except ImportError:
    INTRADAY_CAPITAL_RATIO = 0.50   # 총 자본의 50% → 단타
    SWING_CAPITAL_RATIO    = 0.30   # 총 자본의 30% → 스윙
    RESERVE_RATIO          = 0.20   # 총 자본의 20% → 예비
    SWING_MAX_POSITIONS    = 5      # 스윙 최대 동시 포지션 수
    SWING_MAX_SINGLE_RATIO = 0.30   # 스윙 자본 내 단일 종목 최대 30%


def get_swing_budget() -> float:
    """
    현재 스윙 전략에 사용 가능한 총 예산 계산
    
    Returns:
        float: 주문 가능 금액 (원)
    """
    total_cash = _get_available_cash()
    if total_cash <= 0:
        logger.warning("[RiskMgr] 주문 가능 현금 조회 실패 또는 0원")
        return 0.0

    swing_budget = total_cash * SWING_CAPITAL_RATIO
    logger.debug(
        f"[RiskMgr] 잔고:{total_cash:,.0f} × {SWING_CAPITAL_RATIO:.0%}"
        f" = 스윙예산:{swing_budget:,.0f}"
    )
    return swing_budget


def get_position_budget(swing_budget: float, score: float,
                        total_score: float) -> float:
    """
    개별 종목 투자 금액 계산 (스코어 비례 배분)
    
    Args:
        swing_budget: 스윙 전체 예산
        score       : 해당 종목의 스코어
        total_score : 후보 전체 스코어 합계
    Returns:
        float: 해당 종목에 투자할 금액
    """
    if total_score <= 0:
        return 0.0

    ratio  = score / total_score
    amount = swing_budget * ratio

    # 단일 종목 최대 비율 캡
    max_single = swing_budget * SWING_MAX_SINGLE_RATIO
    amount = min(amount, max_single)

    logger.debug(
        f"[RiskMgr] 포지션예산 | 스코어비율:{ratio:.2%} "
        f"→ {amount:,.0f}원 (상한:{max_single:,.0f})"
    )
    return amount


def can_open_position(symbol: str = None) -> tuple[bool, str]:
    """
    신규 포지션 개설 가능 여부 확인
    
    Returns:
        tuple: (가능여부: bool, 사유: str)
    """
    positions = load_positions()

    # 최대 포지션 수 초과
    if len(positions) >= SWING_MAX_POSITIONS:
        return False, f"최대 포지션 수 초과 ({len(positions)}/{SWING_MAX_POSITIONS})"

    # 이미 보유 중인 종목
    if symbol and symbol in positions:
        return False, f"이미 보유 중인 종목: {symbol}"

    # 예산 확인
    budget = get_swing_budget()
    if budget < 100_000:  # 최소 10만원 미만이면 신규 진입 불가
        return False, f"가용 예산 부족: {budget:,.0f}원"

    return True, "OK"


def calc_quantity(amount: float, price: float) -> int:
    """
    투자 금액과 주가로 매수 수량 계산
    
    Args:
        amount: 투자 금액
        price : 현재가
    Returns:
        int: 매수 수량 (0이면 진입 불가)
    """
    if price <= 0:
        return 0
    qty = int(amount // price)
    return max(0, qty)


def calc_target_stop(entry_price: float, strategy: str) -> tuple[float, float, float]:
    """
    전략별 목표가/손절가/트레일링스톱 계산
    
    Returns:
        tuple: (target_pct, stop_pct, trail_pct)
    """
    _STRATEGY_PARAMS = {
        'MOMENTUM'    : {'target': 0.09, 'stop': 0.04, 'trail': 0.05},
        'REVERSAL'    : {'target': 0.08, 'stop': 0.03, 'trail': 0.04},
        'TREND_FOLLOW': {'target': 0.12, 'stop': 0.05, 'trail': 0.06},
    }
    params = _STRATEGY_PARAMS.get(strategy, {'target': 0.09, 'stop': 0.04, 'trail': 0.05})
    return params['target'], params['stop'], params['trail']


def check_daily_loss_limit(max_loss_pct: float = 0.03) -> bool:
    """
    일일 손실 한도 초과 여부 확인
    - 스윙 자본 풀 기준 일일 -3% 초과 시 신규 진입 중단
    
    Returns:
        bool: True = 한도 초과 (진입 중단), False = 정상
    """
    # TODO: 당일 실현손익 집계 로직 구현 (Phase 3)
    # 현재는 항상 False 반환 (미구현)
    return False


def _get_available_cash() -> float:
    """KIS 주문 가능 현금 조회"""
    url = f"{get_base_url()}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
    headers = get_headers("TTTC8908R")
    params = {
        "CANO"        : os.getenv('KIS_ACCOUNT_NO', ''),
        "ACNT_PRDT_CD": "01",
        "PDNO"        : "005930",   # 더미 종목코드 (필수 파라미터)
        "ORD_UNPR"    : "0",
        "ORD_DVSN"    : "01",
        "CMA_EVLU_AMT_ICLD_YN": "N",
        "OVRS_ICLD_YN": "N",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=8)
        data = resp.json()
        if data.get('rt_cd') != '0':
            logger.warning(f"[RiskMgr] 주문가능금액 조회 오류: {data.get('msg1')}")
            return 0.0
        cash = float(data.get('output', {}).get('ord_psbl_cash', 0) or 0)
        return cash
    except Exception as e:
        logger.error(f"[RiskMgr] 주문가능금액 조회 예외: {e}")
        return 0.0
