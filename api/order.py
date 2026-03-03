# ============================================================
#  api/order.py  –  매수 / 매도 / 정정취소 주문
# ============================================================

import requests
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from auth import get_headers, get_base_url, get_account, IS_REAL
from utils.logger import get_logger

logger = get_logger("order")

# ── TR ID 매핑 ────────────────────────────────────────────────
# 실전: TTTC0802U (매수), TTTC0801U (매도)
# 모의: VTTC0802U (매수), VTTC0801U (매도)
TR_BUY  = "TTTC0802U" if IS_REAL else "VTTC0802U"
TR_SELL = "TTTC0801U" if IS_REAL else "VTTC0801U"
TR_CNCL = "TTTC0803U" if IS_REAL else "VTTC0803U"


def _send_order(tr_id: str, body: dict) -> dict:
    """주문 공통 전송 함수"""
    try:
        res = requests.post(
            f"{get_base_url()}/uapi/domestic-stock/v1/trading/order-cash",
            headers=get_headers(tr_id, {"hashkey": ""}),
            json=body,
            timeout=10,
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        logger.error(f"주문 전송 오류: {e}")
        return {}


def buy_market(stock_code: str, qty: int) -> dict:
    """
    시장가 매수.

    Args:
        stock_code: 종목코드 (예: "005930")
        qty       : 매수 수량

    Returns:
        {"success": True/False, "order_no": "...", "msg": "..."}
    """
    cano, acnt_prdt_cd = get_account()

    body = {
        "CANO"        : cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO"        : stock_code,
        "ORD_DVSN"    : "01",        # 01: 시장가
        "ORD_QTY"     : str(qty),
        "ORD_UNPR"    : "0",         # 시장가는 0
    }

    data = _send_order(TR_BUY, body)
    return _parse_order_result(data, "매수", stock_code, qty)


def buy_limit(stock_code: str, qty: int, price: int) -> dict:
    """
    지정가 매수.

    Args:
        stock_code: 종목코드
        qty       : 매수 수량
        price     : 지정 가격
    """
    cano, acnt_prdt_cd = get_account()

    body = {
        "CANO"        : cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO"        : stock_code,
        "ORD_DVSN"    : "00",        # 00: 지정가
        "ORD_QTY"     : str(qty),
        "ORD_UNPR"    : str(price),
    }

    data = _send_order(TR_BUY, body)
    return _parse_order_result(data, "매수(지정가)", stock_code, qty, price)


def sell_market(stock_code: str, qty: int) -> dict:
    """
    시장가 매도.

    Args:
        stock_code: 종목코드
        qty       : 매도 수량 (전량이면 보유수량 전체)
    """
    cano, acnt_prdt_cd = get_account()

    body = {
        "CANO"        : cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO"        : stock_code,
        "ORD_DVSN"    : "01",        # 01: 시장가
        "ORD_QTY"     : str(qty),
        "ORD_UNPR"    : "0",
    }

    data = _send_order(TR_SELL, body)
    return _parse_order_result(data, "매도", stock_code, qty)


def sell_limit(stock_code: str, qty: int, price: int) -> dict:
    """
    지정가 매도.
    """
    cano, acnt_prdt_cd = get_account()

    body = {
        "CANO"        : cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO"        : stock_code,
        "ORD_DVSN"    : "00",        # 00: 지정가
        "ORD_QTY"     : str(qty),
        "ORD_UNPR"    : str(price),
    }

    data = _send_order(TR_SELL, body)
    return _parse_order_result(data, "매도(지정가)", stock_code, qty, price)


def cancel_order(org_order_no: str, stock_code: str, qty: int) -> dict:
    """
    주문 취소.

    Args:
        org_order_no: 원주문번호
        stock_code  : 종목코드
        qty         : 취소 수량
    """
    cano, acnt_prdt_cd = get_account()

    body = {
        "CANO"          : cano,
        "ACNT_PRDT_CD"  : acnt_prdt_cd,
        "KRX_FWDG_ORD_ORGNO": "",
        "ORGN_ODNO"     : org_order_no,
        "ORD_DVSN"      : "00",
        "RVSE_CNCL_DVSN_CD": "02",  # 02: 취소
        "ORD_QTY"       : str(qty),
        "ORD_UNPR"      : "0",
        "QTY_ALL_ORD_YN": "Y",      # 잔량 전부 취소
    }

    data = _send_order(TR_CNCL, body)
    return _parse_order_result(data, "취소", stock_code, qty)


def calc_buy_qty(price: int, budget: int) -> int:
    """
    예산과 현재가로 매수 가능 수량 계산.

    Args:
        price : 현재가 (원)
        budget: 투자 예산 (원)

    Returns:
        매수 가능 수량 (수수료 0.015% 고려)
    """
    if price <= 0:
        return 0
    fee_rate = 1.00015   # 수수료 고려
    return int(budget / (price * fee_rate))


def _parse_order_result(data: dict, order_type: str,
                        stock_code: str, qty: int,
                        price: int = 0) -> dict:
    """주문 결과 파싱 및 로그"""
    if not data:
        logger.error(f"[{stock_code}] {order_type} 주문 응답 없음")
        return {"success": False, "order_no": "", "msg": "응답 없음"}

    rt_cd    = data.get("rt_cd", "")
    msg      = data.get("msg1", "")
    order_no = data.get("output", {}).get("ODNO", "")

    if rt_cd == "0":
        price_str = f" @{price:,}원" if price else " 시장가"
        logger.info(f"[{stock_code}] ✅ {order_type} 성공 | {qty}주{price_str} | 주문번호: {order_no}")
        return {"success": True, "order_no": order_no, "msg": msg}
    else:
        logger.warning(f"[{stock_code}] ❌ {order_type} 실패 | {msg}")
        return {"success": False, "order_no": "", "msg": msg}


# ── 테스트 ────────────────────────────────────────────────────
if __name__ == "__main__":
    # ⚠️ 아래 테스트는 실제 주문이 발생합니다!
    # 모의투자 계좌로만 테스트하세요.
    print("매수 가능 수량 계산 테스트")
    price  = 75000
    budget = 300_000
    qty    = calc_buy_qty(price, budget)
    print(f"  현재가 {price:,}원 / 예산 {budget:,}원 → {qty}주 매수 가능")

    # 실제 주문 테스트 (주석 해제 후 사용)
    # result = buy_market("005930", 1)
    # print(result)
