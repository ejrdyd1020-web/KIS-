# ============================================================
#  run_forever.py  –  KIS 자동매매 무한 반복 실행기
#
#  실행 방법 (한 번만 실행하면 그 이후 자동 반복):
#    python run_forever.py
#
#  종료 방법:
#    Ctrl+C
#
#  동작 흐름:
#    1. 오늘 주말/공휴일 여부 확인 → 해당되면 다음 영업일까지 대기
#    2. 장 시작(09:00) 전이면 대기
#    3. main() 실행 (장중 자동매매)
#    4. 장 마감 후 다음날 09:00까지 대기
#    5. 무한 반복 (Ctrl+C 전까지)
# ============================================================

import sys
import os
import time
from datetime import datetime, date, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from utils.logger import get_logger
logger = get_logger("run_forever")


# ══════════════════════════════════════════
# 한국 공휴일 (연도별 하드코딩)
# ══════════════════════════════════════════

KR_HOLIDAYS = {
    # 2025년
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30",
    "2025-03-01", "2025-05-05", "2025-05-06", "2025-06-06",
    "2025-08-15", "2025-10-03", "2025-10-05", "2025-10-06", "2025-10-07",
    "2025-10-09", "2025-12-25",

    # 2026년
    "2026-01-01", "2026-01-28", "2026-01-29", "2026-01-30",
    "2026-03-01", "2026-05-05", "2026-06-06",
    "2026-08-15", "2026-09-24", "2026-09-25", "2026-09-26",
    "2026-10-03", "2026-10-09", "2026-12-25",
}


def is_trading_day(d: date = None) -> bool:
    """주말 + 공휴일이면 False (휴장일)"""
    if d is None:
        d = date.today()
    if d.weekday() >= 5:          # 토(5), 일(6)
        return False
    if d.strftime("%Y-%m-%d") in KR_HOLIDAYS:
        return False
    return True


def next_trading_day(d: date = None) -> date:
    """다음 영업일 반환"""
    if d is None:
        d = date.today()
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def wait_until(target: datetime):
    """target 시각까지 대기 (1분 간격으로 남은 시간 로그)"""
    while True:
        now  = datetime.now()
        diff = (target - now).total_seconds()
        if diff <= 0:
            break
        hours, rem = divmod(int(diff), 3600)
        mins        = rem // 60
        logger.info(f"⏳ 대기 중... {target.strftime('%m/%d %H:%M')}까지 {hours}시간 {mins}분 남음")
        time.sleep(min(60, diff))   # 최대 1분 단위로 sleep


# ══════════════════════════════════════════
# 메인 루프
# ══════════════════════════════════════════

def run_forever():
    logger.info("=" * 60)
    logger.info("  KIS 자동매매 무한 반복 실행기 시작")
    logger.info("  종료하려면 Ctrl+C 를 누르세요")
    logger.info("=" * 60)

    while True:
        today = date.today()

        # ── 오늘 휴장일이면 다음 영업일 09:00까지 대기 ──────
        if not is_trading_day(today):
            next_day  = next_trading_day(today)
            target_dt = datetime(next_day.year, next_day.month, next_day.day, 9, 0, 0)
            logger.info(f"📅 오늘({today}) 휴장일 → 다음 영업일 {next_day} 09:00까지 대기")
            wait_until(target_dt)
            continue

        # ── 장 시작(09:00) 전이면 대기 ──────────────────────
        now       = datetime.now()
        market_open = datetime(today.year, today.month, today.day, 9, 0, 0)

        if now < market_open:
            logger.info(f"📅 오늘({today}) 영업일 확인 → 장 시작 09:00까지 대기")
            wait_until(market_open)

        # ── 장 마감 이후면 내일 영업일로 넘김 ───────────────
        market_close = datetime(today.year, today.month, today.day, 15, 35, 0)
        if datetime.now() >= market_close:
            next_day  = next_trading_day(today)
            target_dt = datetime(next_day.year, next_day.month, next_day.day, 9, 0, 0)
            logger.info(f"⏰ 오늘 장 이미 마감 → 다음 영업일 {next_day} 09:00까지 대기")
            wait_until(target_dt)
            continue

        # ── 장 전 스크리닝 (08:30~09:00) ────────────────────
        premarket_time = datetime(today.year, today.month, today.day, 8, 30, 0)
        now_check = datetime.now()
        if now_check < premarket_time:
            logger.info(f"⏳ 장 전 스크리닝 08:30까지 대기...")
            wait_until(premarket_time)

        logger.info("📋 장 전 스크리닝 시작 (ohlcv 캐시 수집 + watchlist 생성)")
        try:
            from auth import get_access_token
            get_access_token()
            from premarket import run_premarket_screening, save_watchlist
            watchlist = run_premarket_screening(top_n=10)
            if watchlist:
                save_watchlist(watchlist)
                logger.info(f"✅ 장 전 스크리닝 완료: {len(watchlist)}개 종목")
            else:
                logger.warning("⚠ 장 전 스크리닝 후보 없음 (ohlcv 캐시는 수집됨)")
        except Exception as e:
            logger.error(f"🚨 장 전 스크리닝 오류: {e} → 스킵 후 매매 진행")

        # ── 09:00까지 남은 시간 대기 ────────────────────────
        market_open2 = datetime(today.year, today.month, today.day, 9, 0, 0)
        if datetime.now() < market_open2:
            wait_until(market_open2)

        # ── 자동매매 실행 (오류 시 2분 대기 후 장 중이면 재시작) ──
        RESTART_WAIT = 120   # 2분
        RESTART_LIMIT = 5    # 하루 최대 재시작 횟수

        restart_count = 0
        while True:
            logger.info(f"\n{'='*60}")
            if restart_count == 0:
                logger.info(f"  🚀 {today} 자동매매 시작!")
            else:
                logger.info(f"  🔄 {today} 자동매매 재시작 (#{restart_count})")
            logger.info(f"{'='*60}\n")

            try:
                from main import main
                main()
                # 정상 종료 → 루프 탈출
                break
            except SystemExit:
                logger.info("main() 정상 종료 (sys.exit)")
                break
            except Exception as e:
                logger.error(f"🚨 main() 예외 발생: {e}")
                import traceback
                logger.error(traceback.format_exc())

                # 장 마감 이후면 재시작 불필요
                market_close = datetime(today.year, today.month, today.day, 15, 35, 0)
                if datetime.now() >= market_close:
                    logger.info("  → 장 마감 이후, 재시작 하지 않음")
                    break

                # 재시작 횟수 초과
                restart_count += 1
                if restart_count > RESTART_LIMIT:
                    logger.error(f"  → 재시작 {RESTART_LIMIT}회 초과, 오늘 매매 중단")
                    break

                logger.info(f"  → 시스템 멈춤 감지. {RESTART_WAIT//60}분 후 자동 재시작... (#{restart_count}/{RESTART_LIMIT})")
                time.sleep(RESTART_WAIT)

        # ── 오늘 매매 종료 → 내일 영업일 09:00까지 대기 ─────
        next_day  = next_trading_day(today)
        target_dt = datetime(next_day.year, next_day.month, next_day.day, 9, 0, 0)
        logger.info(f"\n{'='*60}")
        logger.info(f"  ✅ {today} 매매 완료")
        logger.info(f"  다음 영업일: {next_day} 09:00에 자동 재시작")
        logger.info(f"{'='*60}\n")
        wait_until(target_dt)


if __name__ == "__main__":
    try:
        run_forever()
    except KeyboardInterrupt:
        logger.info("\n👋 run_forever 종료 (Ctrl+C)")
        sys.exit(0)
