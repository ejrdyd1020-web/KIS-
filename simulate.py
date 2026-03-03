# ============================================================
#  simulate.py  –  실제 주문 없이 전략 시뮬레이션
#  실행: python simulate.py
#  
#  ※ 실제 주문은 발생하지 않습니다.
#     조건 검색 → 매수 후보 → 손절/익절 시뮬레이션만 수행
# ============================================================

import time
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from auth                import get_access_token
from api.price           import get_fluctuation_rank, get_current_price
from api.balance         import get_balance
from strategy.condition  import filter_candidates, score_candidate
from utils.logger        import get_logger
from config import (
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    ORDER_AMOUNT,
    MAX_POSITIONS,
    CONDITION,
)

logger = get_logger("simulate")

# ── 가상 포지션 저장소 ────────────────────────────────────────
_sim_positions: dict[str, dict] = {}
_sim_log: list[str] = []


def sim_buy(stock: dict) -> bool:
    """가상 매수"""
    code  = stock["code"]
    name  = stock["name"]
    price = stock["price"]

    if len(_sim_positions) >= MAX_POSITIONS:
        return False

    budget = ORDER_AMOUNT
    qty    = int(budget / price)
    if qty <= 0:
        return False

    stop_loss   = int(price * (1 + STOP_LOSS_PCT   / 100))
    take_profit = int(price * (1 + TAKE_PROFIT_PCT / 100))

    _sim_positions[code] = {
        "code"       : code,
        "name"       : name,
        "qty"        : qty,
        "avg_price"  : price,
        "stop_loss"  : stop_loss,
        "take_profit": take_profit,
        "bought_at"  : datetime.now(),
    }

    msg = (f"[가상매수] {name}({code}) | "
           f"{qty}주 × {price:,}원 = {qty*price:,}원 | "
           f"손절가: {stop_loss:,} | 익절가: {take_profit:,}")
    logger.info(msg)
    _sim_log.append(msg)
    return True


def sim_sell(code: str, cur_price: int, reason: str):
    """가상 매도"""
    if code not in _sim_positions:
        return

    pos       = _sim_positions[code]
    avg_price = pos["avg_price"]
    qty       = pos["qty"]
    profit    = (cur_price - avg_price) * qty
    profit_pct= (cur_price - avg_price) / avg_price * 100

    reason_map = {
        "stop_loss"   : "🔴 손절",
        "take_profit" : "🟢 익절",
        "market_close": "⏰ 장마감",
    }
    label = reason_map.get(reason, reason)

    msg = (f"[가상매도] {pos['name']}({code}) | {label} | "
           f"매입가: {avg_price:,} → 현재가: {cur_price:,} | "
           f"수익: {profit:+,}원 ({profit_pct:+.2f}%)")
    logger.info(msg)
    _sim_log.append(msg)
    del _sim_positions[code]


def print_sim_positions():
    """현재 가상 포지션 출력"""
    print(f"\n{'='*65}")
    print(f"  📋 가상 보유 포지션 ({len(_sim_positions)}개)")
    print(f"{'='*65}")

    if not _sim_positions:
        print("  보유 포지션 없음")
    else:
        total_profit = 0
        for pos in _sim_positions.values():
            info = get_current_price(pos["code"])
            cur  = info.get("price", pos["avg_price"]) if info else pos["avg_price"]
            pct  = (cur - pos["avg_price"]) / pos["avg_price"] * 100
            profit = (cur - pos["avg_price"]) * pos["qty"]
            total_profit += profit
            sign = "+" if pct >= 0 else ""
            print(f"  {pos['name']} ({pos['code']})")
            print(f"    수량: {pos['qty']}주 | 매입가: {pos['avg_price']:,} | 현재가: {cur:,}")
            print(f"    손절가: {pos['stop_loss']:,} | 익절가: {pos['take_profit']:,}")
            print(f"    평가손익: {profit:+,}원 ({sign}{pct:.2f}%)")
        sign = "+" if total_profit >= 0 else ""
        print(f"\n  💰 총 평가손익: {sign}{total_profit:,}원")
    print(f"{'='*65}\n")


def print_sim_summary():
    """시뮬레이션 결과 요약 출력"""
    print(f"\n{'='*65}")
    print(f"  📊 시뮬레이션 결과 요약")
    print(f"{'='*65}")

    buys   = [l for l in _sim_log if "가상매수" in l]
    sells  = [l for l in _sim_log if "가상매도" in l]
    wins   = [l for l in sells if "익절" in l]
    losses = [l for l in sells if "손절" in l]

    print(f"  총 매수 횟수  : {len(buys)}회")
    print(f"  총 매도 횟수  : {len(sells)}회")
    print(f"  익절          : {len(wins)}회")
    print(f"  손절          : {len(losses)}회")
    if sells:
        win_rate = len(wins) / len(sells) * 100
        print(f"  승률          : {win_rate:.1f}%")

    print(f"\n  전체 거래 내역:")
    for log in _sim_log:
        print(f"    {log}")
    print(f"{'='*65}\n")


def run_simulation(duration_minutes: int = 30):
    """
    시뮬레이션 실행.

    Args:
        duration_minutes: 시뮬레이션 실행 시간 (분), 기본 30분
    """
    print(f"""
╔══════════════════════════════════════════════════════╗
║         KIS 자동매매 시뮬레이션 모드                 ║
║         ※ 실제 주문은 발생하지 않습니다             ║
╚══════════════════════════════════════════════════════╝
  시작 시간  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
  실행 시간  : {duration_minutes}분
  손절 기준  : {STOP_LOSS_PCT:+.1f}%
  익절 기준  : {TAKE_PROFIT_PCT:+.1f}%
  종목당 예산: {ORDER_AMOUNT:,}원
  최대 종목  : {MAX_POSITIONS}개
""")

    SCAN_INTERVAL = 30   # 30초마다 스캔
    CHECK_INTERVAL = 5   # 5초마다 시세 체크
    end_time = time.time() + duration_minutes * 60
    last_scan = 0

    try:
        while time.time() < end_time:
            now = datetime.now().strftime("%H:%M")

            # ── 조건 검색 + 가상 매수 (30초마다) ──────────────
            if time.time() - last_scan >= SCAN_INTERVAL:
                last_scan = time.time()
                logger.info(f"[{now}] 🔍 조건 검색 중...")

                stocks = get_fluctuation_rank(top_n=30)
                if stocks:
                    candidates = filter_candidates(stocks)
                    candidates.sort(key=lambda x: score_candidate(x), reverse=True)

                    if candidates:
                        print(f"\n  📌 매수 후보 {len(candidates)}개 발견:")


                        # 상위 후보 가상 매수
                        for stock in candidates:
                            if len(_sim_positions) >= MAX_POSITIONS:
                                break
                            if stock["code"] not in _sim_positions:
                                # [추가] 매수 시도 전 서버 부하 방지를 위해 아주 짧게 휴식
                                time.sleep(0.2)
                                sim_buy(stock)
                    else:
                        logger.info("매수 후보 없음")
                else:
                    logger.warning("등락률 순위 조회 실패")

            # ── 포지션 손절/익절 체크 (5초마다) ───────────────
            for code, pos in list(_sim_positions.items()):
                # [추가] 반복문이 너무 빠르게 돌지 않도록 지연 시간 추가
                time.sleep(0.2)
                info = get_current_price(code)
                if not info:
                    continue

                cur_price = info["price"]
                pct = (cur_price - pos["avg_price"]) / pos["avg_price"] * 100

                if cur_price <= pos["stop_loss"]:
                    sim_sell(code, cur_price, "stop_loss")
                elif cur_price >= pos["take_profit"]:
                    sim_sell(code, cur_price, "take_profit")
                else:
                    logger.debug(f"[{pos['name']}] 현재가: {cur_price:,} ({pct:+.2f}%) 유지")

            # 5분마다 포지션 현황 출력
            if datetime.now().minute % 5 == 0 and datetime.now().second < CHECK_INTERVAL:
                print_sim_positions()

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n시뮬레이션 중단 (Ctrl+C)")

    # 남은 포지션 강제 청산
    for code, pos in list(_sim_positions.items()):
        info = get_current_price(code)
        cur  = info.get("price", pos["avg_price"]) if info else pos["avg_price"]
        sim_sell(code, cur, "market_close")

    print_sim_summary()


# ── 메인 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    # 토큰 발급
    try:
        get_access_token()
    except Exception as e:
        print(f"토큰 발급 실패: {e}")
        sys.exit(1)

    # 계좌 잔고 확인
    data = get_balance()
    if data:
        print(f"  💰 예수금: {data['deposit']:,}원")

    # 시뮬레이션 실행 (기본 30분)
    run_simulation(duration_minutes=120)
