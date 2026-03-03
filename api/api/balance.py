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
            "deposit"    : 1000000,   # 예수금 (D+2)
            "total_eval" : 1750000,   # 총 평가금액
            "total_profit": 30000,    # 총 평가손익
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
        return {
            "stocks"      : stocks,
            "deposit"     : int(summary.get("dnca_tot_amt", 0)),
            "total_eval"  : int(summary.get("tot_evlu_amt", 0)),
            "total_profit": int(summary.get("evlu_pfls_smtl_amt", 0)),
        }

    except Exception as e:
        logger.error(f"잔고 조회 오류: {e}")
        return {}


def get_deposit() -> int:
    """예수금(D+2) 빠른 조회"""
    result = get_balance()
    return result.get("deposit", 0)


# ── 테스트 ────────────────────────────────────────────────────
if __name__ == "__main__":
    data = get_balance()
    if not data:
        print("조회 실패")
    else:
        stocks = data["stocks"]
        print(f"\n{'='*55}")
        print(f"  📊 잔고 현황")
        print(f"{'='*55}")
        if not stocks:
            print("  보유 종목 없음")
        else:
            for s in stocks:
                sign = "+" if s["profit_amt"] >= 0 else ""
                print(f"  {s['name']} ({s['code']})")
                print(f"    수량: {s['qty']:,}주 | 평균단가: {s['avg_price']:,}원 | 현재가: {s['cur_price']:,}원")
                print(f"    평가손익: {sign}{s['profit_amt']:,}원 ({sign}{s['profit_pct']:.2f}%)")
        print(f"\n  💰 예수금    : {data['deposit']:>15,}원")
        print(f"  📈 총평가금액: {data['total_eval']:>15,}원")
        sign = "+" if data['total_profit'] >= 0 else ""
        print(f"  {'📉' if data['total_profit'] < 0 else '📈'} 총평가손익: {sign}{data['total_profit']:>14,}원")
        print(f"{'='*55}")
