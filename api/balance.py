# ============================================================
#  api/balance.py  –  잔고 / 예수금 조회
# ============================================================

import requests
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from auth import get_headers, get_base_url, get_account, IS_REAL
from utils.logger import get_logger

logger = get_logger("balance")


def get_balance() -> dict:
    """
    주식 잔고 조회.

    반환값:
        {
            "stocks": [
                {
                    "code"      : "005930",
                    "name"      : "삼성전자",
                    "qty"       : 10,
                    "avg_price" : 72000,
                    "cur_price" : 75000,
                    "eval_amt"  : 750000,
                    "profit_amt": 30000,
                    "profit_pct": 4.17,
                }
            ],
            "deposit"    : 1000000,
            "total_eval" : 1750000,
            "total_profit": 30000,
        }
    """
    cano, acnt_prdt_cd = get_account()
    tr_id = "TTTC8434R" if IS_REAL else "VTTC8434R"

    params = {
        "CANO"                 : cano,
        "ACNT_PRDT_CD"         : acnt_prdt_cd,
        "AFHR_FLPR_YN"         : "N",
        "OFL_YN"               : "",
        "INQR_DVSN"            : "02",
        "UNPR_DVSN"            : "01",
        "FUND_STTL_ICLD_YN"   : "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN"            : "01",
        "CTX_AREA_FK100"       : "",
        "CTX_AREA_NK100"       : "",
    }

    try:
        res = requests.get(
            f"{get_base_url()}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=get_headers(tr_id),
            params=params,
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            logger.warning(f"잔고 조회 실패: {data.get('msg1')}")
            return {}

        stocks = []
        for s in data.get("output1", []):
            qty = int(s.get("hldg_qty", 0))
            if qty == 0:
                continue
            stocks.append({
                "code"      : s.get("pdno", ""),
                "name"      : s.get("prdt_name", ""),
                "qty"       : qty,
                "avg_price" : int(float(s.get("pchs_avg_pric", 0))),
                "cur_price" : int(s.get("prpr", 0)),
                "eval_amt"  : int(s.get("evlu_amt", 0)),
                "profit_amt": int(s.get("evlu_pfls_amt", 0)),
                "profit_pct": float(s.get("evlu_pfls_rt", 0)),
            })

        summary = data.get("output2", [{}])[0]

        # ── 매수 가능 금액 계산 ───────────────────────────────
        # thdt_buy_able_amt : KIS가 직접 계산해주는 당일 매수가능금액
        #                     D+0 예수금 + D+1 정산예정 + D+2 미결제 모두 포함
        # dnca_tot_amt      : 순수 예수금(D+0)만 → 실제보다 적게 잡힘 (기존 문제)
        # nxdy_excc_amt     : D+1 익일 정산 예정금액 (참고용으로만 노출)
        buyable   = int(summary.get("thdt_buy_able_amt", 0))
        d0        = int(summary.get("dnca_tot_amt", 0))
        d1        = int(summary.get("nxdy_excc_amt", 0))

        # thdt_buy_able_amt 가 0이면 API 미지원 계정 → fallback
        deposit = buyable if buyable > 0 else d0

        logger.debug(
            f"매수가능금액: {deposit:,}원 "
            f"(D+0 예수금: {d0:,} / D+1 정산예정: {d1:,} / KIS합산: {buyable:,})"
        )

        return {
            "stocks"      : stocks,
            "deposit"     : deposit,          # 실제 매수가능금액 (D+2 포함)
            "deposit_d0"  : d0,               # 순수 예수금 (참고용)
            "deposit_d1"  : d1,               # D+1 정산예정 (참고용)
            "total_eval"  : int(summary.get("tot_evlu_amt", 0)),
            "total_profit": int(summary.get("evlu_pfls_smtl_amt", 0)),
        }

    except Exception as e:
        logger.error(f"잔고 조회 오류: {e}")
        return {}


def get_deposit() -> int:
    """
    당일 매수가능금액 반환 (D+2 미결제 포함).

    KIS API thdt_buy_able_amt 필드 사용.
    → 당일 매도한 종목의 미결제 금액도 즉시 재매수에 사용 가능.
    """
    result = get_balance()
    return result.get("deposit", 0)


def get_deposit_detail() -> dict:
    """
    예수금 상세 조회 (디버깅 / 리포트용).

    반환값:
        {
            "buyable" : 1_050_000,   # 실제 매수가능 (D+2 포함)
            "d0"      : 800_000,     # 순수 예수금
            "d1"      : 250_000,     # D+1 정산예정
        }
    """
    result = get_balance()
    return {
        "buyable": result.get("deposit",     0),
        "d0"     : result.get("deposit_d0",  0),
        "d1"     : result.get("deposit_d1",  0),
    }
