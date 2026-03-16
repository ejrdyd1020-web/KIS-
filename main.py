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

# Windows 콘솔 cp949 인코딩 문제 해결: stdout/stderr를 utf-8로 강제 설정
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

current_path = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_path)

load_dotenv(os.path.join(current_path, ".env"))

from auth                  import get_access_token
from api.balance           import get_balance
from api.ohlcv             import load_ohlcv_cache, fetch_and_save_ohlcv

from strategy.position           import run_monitor, sync_positions_from_balance, print_positions
from strategy.strategy_breakout  import run_breakout
from strategy.strategy_reversion import run_reversion
from strategy.condition          import load_bought_codes

from utils.logger import get_logger
from config import (
    MARKET_OPEN, MARKET_CLOSE, TOTAL_BUDGET,
    BREAKOUT, REVERSION, MARKET_PHASE,
    STRATEGY_BREAKOUT, STRATEGY_REVERSION, STRATEGY_HALT,
)

try:
    from api.index import get_market_phase, calc_strategy_budget, calc_position_budgets, BULL, BEAR
except ImportError:
    from index import get_market_phase, calc_strategy_budget, calc_position_budgets, BULL, BEAR

logger = get_logger("main")


def get_current_strategy() -> str:
    """
    현재 시각 기준으로 활성 전략 반환.

    Returns:
        STRATEGY_BREAKOUT  : 09:00 ~ 09:10  공격형 주도주 포착
        STRATEGY_REVERSION : 09:10 ~ 15:20  방어형 눌림목 매매
        STRATEGY_HALT      : 그 외           매매 중단
    """
    now = datetime.now().strftime("%H:%M")
    if BREAKOUT["start_time"] <= now < BREAKOUT["end_time"]:
        return STRATEGY_BREAKOUT
    elif REVERSION["start_time"] <= now < REVERSION["end_time"]:
        return STRATEGY_REVERSION
    else:
        return STRATEGY_HALT


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
    data    = get_balance()
    deposit = data.get("deposit", 0) if data else 0

    print(f"""
======================================================
         KIS 단타 자동매매 프로그램 시작
======================================================
  시작 시간  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
  장 운영    : {MARKET_OPEN} ~ {MARKET_CLOSE}
  전략 A     : BREAKOUT  {BREAKOUT['start_time']} ~ {BREAKOUT['end_time']}  (익절 +{BREAKOUT['take_profit_pct']}% / 손절 {BREAKOUT['stop_loss_pct']}%)
  전략 B     : REVERSION {REVERSION['start_time']} ~ {REVERSION['end_time']}  (익절 +{REVERSION['take_profit_pct']}% / 손절 {REVERSION['stop_loss_pct']}%)
  총 예산    : {TOTAL_BUDGET:,}원
""")

    if data:
        print(f"  [+] 매수가능금액 : {deposit:,}원")
        print(f"  [*] 보유 종목    : {len(data.get('stocks', []))}개")
        print()


def main():
    # ── 1. 토큰 발급 ──────────────────────────────────────────
    logger.info("토큰 발급 중...")
    try:
        get_access_token()
    except Exception as e:
        logger.error(f"토큰 발급 실패: {e}")
        sys.exit(1)

    # ── 1-1. 전일 OHLCV 캐시 로드 ────────────────────────────
    logger.info("📂 전일 OHLCV 캐시 로드 중...")
    cached_cnt = load_ohlcv_cache()
    if cached_cnt == 0:
        logger.info("캐시 없음 → 장전 스캔 시 수집 예정")

    # ── 2. 시작 정보 출력 ─────────────────────────────────────
    print_startup_info()

    # ── 3. 기존 보유 종목 포지션 복원 ─────────────────────────
    logger.info("기존 보유 종목 포지션 복원 중...")
    sync_positions_from_balance()
    print_positions()

    # ── 3-1. 당일 매수 종목 복원 (재시작 재매수 방지) ─────────
    load_bought_codes()

    # ── 4. 장전 스캔 (08:30~09:00 구간이면 즉시 실행) ─────────
    now = datetime.now().strftime("%H:%M")
    if "08:30" <= now < MARKET_OPEN:
        logger.info("📋 장전 스캔 실행 중...")
        try:
            from premarket import run_premarket_screening, save_watchlist
            watchlist = run_premarket_screening(top_n=10)
            if watchlist:
                save_watchlist(watchlist)
                # watchlist 종목 전일 OHLCV 일괄 수집
                wl_codes = [s["code"] for s in watchlist]
                logger.info(f"📊 watchlist {len(wl_codes)}개 종목 전일 OHLCV 수집 중...")
                fetch_and_save_ohlcv(wl_codes)
        except Exception as e:
            logger.error(f"장전 스캔 오류: {e}")

    # ── 5. 시장 국면 판단 + 전략별 자금 배분 계산 ────────────
    logger.info("📊 시장 국면 판단 중...")
    try:
        phase  = get_market_phase()
        budget = calc_strategy_budget(phase)
    except Exception as e:
        logger.error(f"시장 국면 판단 오류: {e} → BEAR 기본값 적용")
        phase  = BEAR
        budget = calc_strategy_budget(BEAR)

    breakout_budget  = budget["breakout_total"]
    reversion_budget = budget["reversion_total"]

    logger.info(
        f"📊 시장 국면: {phase} | "
        f"BREAKOUT {breakout_budget:,}원 / REVERSION {reversion_budget:,}원"
    )

    # ── 6. 장 시작 대기 ───────────────────────────────────────
    now = datetime.now().strftime("%H:%M")
    if now < MARKET_OPEN:
        wait_for_market_open()

    # ── 7. 스레드 시작 ────────────────────────────────────────
    stop_event = threading.Event()

    current_strat = get_current_strategy()
    logger.info(f"🎯 현재 활성 전략: {current_strat}")

    # 전략 A — BREAKOUT (09:00~09:10)
    breakout_thread = threading.Thread(
        target=run_breakout,
        args=(stop_event, breakout_budget),
        name="breakout",
        daemon=True,
    )

    # 전략 B — REVERSION (09:10~15:20)
    reversion_thread = threading.Thread(
        target=run_reversion,
        args=(stop_event, reversion_budget),
        name="reversion",
        daemon=True,
    )

    # 손절/익절 모니터링
    monitor_thread = threading.Thread(
        target=run_monitor,
        args=(stop_event,),
        name="monitor",
        daemon=True,
    )

    breakout_thread.start()
    reversion_thread.start()
    monitor_thread.start()

    logger.info("✅ 자동매매 시작! BREAKOUT + REVERSION + 모니터 스레드 가동 (종료: Ctrl+C)")

    # ── 8. 메인 루프 ──────────────────────────────────────────
    _last_strategy = ""   # 전략 전환 감지용

    try:
        while True:
            now = datetime.now().strftime("%H:%M")

            # ── 스레드 생존 감시 ──────────────────────────────
            # 모니터 스레드가 죽으면 손절/익절 불가 → 즉시 중단
            if not monitor_thread.is_alive():
                logger.error("🚨 모니터 스레드 비정상 종료 → 자동매매 긴급 중단!")
                stop_event.set()
                break

            # 전략 스레드가 모두 죽었으면 경고 (모니터는 살아있으므로 청산은 가능)
            if not breakout_thread.is_alive() and not reversion_thread.is_alive():
                logger.warning("⚠️ 전략 스레드 모두 종료 → 신규 매수 없음 (모니터는 유지)")

            # 전략 전환 감지 → 로그 출력
            current_strat = get_current_strategy()
            if current_strat != _last_strategy:
                logger.info(f"🔀 전략 전환: {_last_strategy or '시작'} → {current_strat}")
                _last_strategy = current_strat

            # 장 마감 → stop_event 세팅 후 monitor 스레드가 강제청산 처리
            if now >= MARKET_CLOSE:
                logger.info(f"⏰ 장 마감 ({MARKET_CLOSE}) → 모니터 스레드 청산 대기 중...")
                stop_event.set()
                time.sleep(10)
                break

            # 5분마다 포지션 현황 출력
            if datetime.now().minute % 5 == 0 and datetime.now().second < 5:
                print_positions()

            time.sleep(5)

    except KeyboardInterrupt:
        logger.info("사용자가 프로그램을 종료했습니다 (Ctrl+C)")
        stop_event.set()
        time.sleep(3)
    except Exception as e:
        logger.error(f"🚨 메인 루프 예외 발생: {e} → 자동매매 긴급 중단")
        stop_event.set()
        time.sleep(3)

    logger.info("👋 자동매매 프로그램 종료")


if __name__ == "__main__":
    main()
