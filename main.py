# ============================================================
#  main.py  –  KIS 단타 자동매매 메인 실행
# ============================================================
#
#  실행 방법:
#    cd C:\Users\kmg83\AppData\Roaming\Claude\kis_autotrader
#    python main.py
#
# ============================================================

import threading
import time
import sys
import os
from datetime import datetime
from dotenv import load_dotenv

# [추가] 현재 파일이 있는 경로를 시스템 경로에 추가하여 모듈 인식 문제 해결
current_path = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_path)

# .env 로드 (상위 폴더에 있는 경우를 대비)
load_dotenv(os.path.join(current_path, ".env"))

from auth                  import get_access_token
from api.balance           import get_balance

# [수정] 경로 문제를 방지하기 위해 직접 임포트
try:
    from position          import run_monitor, sync_positions_from_balance, print_positions
    from condition         import run_strategy, print_candidates
except ImportError:
    # 만약 폴더 구조가 strategy/ 안에 있다면 아래와 같이 시도
    from strategy.position import run_monitor, sync_positions_from_balance, print_positions
    from strategy.condition import run_strategy, print_candidates

from utils.logger          import get_logger
from config import MARKET_OPEN, MARKET_CLOSE, TOTAL_BUDGET

logger = get_logger("main")


def wait_for_market_open():
    """장 시작 전 대기"""
    while True:
        now = datetime.now().strftime("%H:%M")
        if now >= MARKET_OPEN:
            break
        logger.info(f"⏳ 장 시작 대기 중... (현재: {now} / 시작: {MARKET_OPEN})")
        time.sleep(30)


def print_startup_info():
    """시작 시 계좌 정보 출력"""
    print(f"""
╔══════════════════════════════════════════════════════╗
║         KIS 단타 자동매매 프로그램 시작              ║
╚══════════════════════════════════════════════════════╝
  시작 시간  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
  운용 예산  : {TOTAL_BUDGET:,}원
  장 운영    : {MARKET_OPEN} ~ {MARKET_CLOSE}
""")

    data = get_balance()
    if data:
        print(f"  💰 현재 예수금 : {data['deposit']:,}원")
        print(f"  📦 보유 종목   : {len(data['stocks'])}개")
        print()


def main():
    # ── 1. 토큰 발급 ──────────────────────────────────────────
    logger.info("토큰 발급 중...")
    try:
        get_access_token()
    except Exception as e:
        logger.error(f"토큰 발급 실패: {e}")
        sys.exit(1)

    # ── 2. 시작 정보 출력 ─────────────────────────────────────
    print_startup_info()

    # ── 3. 기존 보유 종목 포지션 복원 ─────────────────────────
    logger.info("기존 보유 종목 포지션 복원 중...")
    sync_positions_from_balance()
    print_positions()

    # ── 4. 장 시작 대기 ───────────────────────────────────────
    now = datetime.now().strftime("%H:%M")
    if now < MARKET_OPEN:
        wait_for_market_open()

    # ── 5. 스레드 시작 ────────────────────────────────────────
    stop_event = threading.Event()

    # 조건 검색 + 매수 스레드
    strategy_thread = threading.Thread(
        target=run_strategy,
        args=(stop_event,),
        name="strategy",
        daemon=True,
    )

    # 손절/익절 모니터링 스레드
    monitor_thread = threading.Thread(
        target=run_monitor,
        args=(stop_event,),
        name="monitor",
        daemon=True,
    )

    strategy_thread.start()
    monitor_thread.start()

    logger.info("✅ 자동매매 시작! (종료: Ctrl+C)")

    # ── 6. 메인 루프 ──────────────────────────────────────────
    try:
        while True:
            now = datetime.now().strftime("%H:%M")

            # 장 마감 후 자동 종료
            if now >= MARKET_CLOSE:
                logger.info(f"⏰ 장 마감 ({MARKET_CLOSE}) - 보유 포지션 전량 청산 중...")
                stop_event.set()
                # 보유 포지션 전량 시장가 매도
                from api.balance import get_balance
                from api.order import sell_market
                balance = get_balance()
                for stock in balance.get("stocks", []):
                    code = stock["code"]
                    name = stock["name"]
                    qty  = stock["qty"]
                    if qty > 0:
                        logger.info(f"[{name}] 장마감 청산 매도: {qty}주")
                        result = sell_market(code, qty)
                        if result.get("success"):
                            logger.info(f"[{name}] ✅ 청산 완료")
                        else:
                            logger.warning(f"[{name}] ❌ 청산 실패: {result.get('msg')}")
                time.sleep(5)
                break

            # 5분마다 포지션 현황 출력
            if datetime.now().minute % 5 == 0 and datetime.now().second < 5:
                print_positions()

            time.sleep(5)

    except KeyboardInterrupt:
        logger.info("사용자가 프로그램을 종료했습니다 (Ctrl+C)")
        stop_event.set()
        time.sleep(3)

    logger.info("👋 자동매매 프로그램 종료")


if __name__ == "__main__":
    main()
