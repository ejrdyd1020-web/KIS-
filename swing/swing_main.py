"""
swing/swing_main.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
스윙 자동매매 독립 메인 루프

실행 방법 (단타 main.py와 완전히 별도 프로세스):
    python swing/swing_main.py

스케줄 (Task Scheduler → setup_swing_scheduler.ps1):
    08:50 → premarket.py swing scan (watchlist_swing.json 생성)
    09:00 → swing_main.py 시작
    15:30 → 자동 종료

단타 main.py와 공유:
    - auth.py (인증)
    - api/chart.py (일봉 메서드)
    - shared/symbol_lock.py (종목 충돌 방지)

단타 main.py와 완전 독립:
    - 별도 Python 프로세스
    - data/positions_swing.json (포지션 파일 분리)
    - data/watchlist_swing.json (후보 파일 분리)
    - logs/swing_YYYYMMDD.log (로그 파일 분리)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
import time
import logging
import signal
from datetime import datetime, time as dtime

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.symbol_lock import release_all_by_strategy, cleanup_stale_locks
from swing.swing_scanner import load_watchlist
from swing.swing_position_manager import load_positions, reconcile_with_kis
from swing.swing_executor import try_buy, check_and_exit, exit_all_positions
from swing.swing_risk import can_open_position

# ── 로그 설정 (단타와 분리된 파일) ─────────────────────────────
_LOG_DIR  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, f"swing_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger('swing_main')

# ── 실행 시간 설정 ──────────────────────────────────────────────
_ENTRY_START  = dtime(9,  0)    # 진입 시작
_ENTRY_END    = dtime(9, 30)    # 진입 종료
_MONITOR_END  = dtime(15, 10)   # 모니터링 종료 (장 마감 20분 전)
_FORCE_EXIT   = dtime(15, 20)   # 강제 청산 (당일 보유 금지 없지만 안전마진)
_SHUTDOWN     = dtime(15, 30)   # 프로세스 종료

_ENTRY_INTERVAL   = 30    # 진입 루프 주기 (초)
_MONITOR_INTERVAL = 60    # 모니터링 루프 주기 (초) - 단타보다 여유
_PRICE_INTERVAL   = 0.2   # API 호출 간격 (초)

_running = True


def _handle_signal(sig, frame):
    """SIGINT / SIGTERM 처리"""
    global _running
    logger.info(f"[swing_main] 종료 신호 수신 ({sig})")
    _running = False


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def main():
    global _running
    logger.info("=" * 60)
    logger.info("KIS AutoTrader - 스윙 전략 시작")
    logger.info(f"PID: {os.getpid()}")
    logger.info("=" * 60)

    # ── 시작 전 준비 ────────────────────────────────────────────
    # 1. 오래된 락 정리 (장애 후 재시작 대비)
    stale = cleanup_stale_locks(max_hours=20)
    if stale:
        logger.warning(f"[swing_main] 오래된 락 정리: {stale}")

    # 2. KIS 실제 잔고 대사
    reconcile_result = reconcile_with_kis()
    if reconcile_result.get('removed'):
        logger.warning(f"[swing_main] 대사 후 제거된 포지션: {reconcile_result['removed']}")
    if reconcile_result.get('orphaned'):
        logger.warning(f"[swing_main] 미등록 보유 종목 (수동 확인 필요): {reconcile_result['orphaned']}")

    # 3. 오늘의 스윙 후보 로드
    watchlist = load_watchlist()
    if not watchlist:
        logger.error("[swing_main] watchlist_swing.json 없음. premarket.py 실행 확인 필요")
        return

    scanned_at = watchlist.get('scanned_at', 'N/A')
    total      = watchlist.get('total', 0)
    logger.info(f"[swing_main] 후보 로드: {total}개 ({scanned_at})")

    # 전략별 후보 목록 + 전체 스코어 합 계산
    candidates = _build_candidate_list(watchlist)
    total_score = sum(c['score'] for c in candidates)
    logger.info(
        f"[swing_main] 후보 상세 | "
        f"MOM:{len(watchlist.get('MOMENTUM',[]))} "
        f"REV:{len(watchlist.get('REVERSAL',[]))} "
        f"TRF:{len(watchlist.get('TREND_FOLLOW',[]))}"
    )

    # ── 메인 루프 ────────────────────────────────────────────────
    while _running:
        now  = datetime.now()
        t    = now.time()

        # 종료 시간
        if t >= _SHUTDOWN:
            logger.info("[swing_main] 장 종료 → 프로세스 종료")
            break

        # 강제 청산 (15:20 이후 보유 포지션 전량 청산)
        if t >= _FORCE_EXIT:
            positions = load_positions()
            if positions:
                logger.warning("[swing_main] 15:20 → 미청산 포지션 강제 청산")
                exit_all_positions(reason='EOD_FORCE')
            time.sleep(30)
            continue

        # 모니터링 종료 시간 이후
        if t >= _MONITOR_END:
            time.sleep(30)
            continue

        # ── 진입 구간 (09:00~09:30) ──────────────────────────
        if _ENTRY_START <= t < _ENTRY_END:
            _run_entry_loop(candidates, total_score)
            time.sleep(_ENTRY_INTERVAL)
            continue

        # ── 보유 포지션 모니터링 (09:30~15:10) ───────────────
        if t >= _ENTRY_END:
            _run_monitor_loop()
            time.sleep(_MONITOR_INTERVAL)
            continue

        # 09:00 이전 대기
        logger.info(f"[swing_main] 장 시작 대기 중... ({t.strftime('%H:%M:%S')})")
        time.sleep(30)

    # ── 종료 처리 ────────────────────────────────────────────────
    _shutdown()


def _run_entry_loop(candidates: list, total_score: float):
    """09:00~09:30 신규 진입 루프"""
    ok, reason = can_open_position()
    if not ok:
        logger.info(f"[swing_main] 신규 진입 불가: {reason}")
        return

    positions = load_positions()
    already_holding = set(positions.keys())

    entered = 0
    for c in candidates:
        symbol   = c['symbol']
        strategy = c['strategy']
        name     = c.get('name', symbol)
        score    = c['score']

        if symbol in already_holding:
            continue

        success = try_buy(
            symbol=symbol,
            name=name,
            strategy=strategy,
            score=score,
            total_score=total_score,
        )
        if success:
            entered += 1
            already_holding.add(symbol)

        time.sleep(_PRICE_INTERVAL)

    if entered:
        logger.info(f"[swing_main] 진입 루프 완료: {entered}개 신규 진입")


def _run_monitor_loop():
    """보유 포지션 모니터링 및 청산 체크"""
    positions = load_positions()
    if not positions:
        return

    exited = 0
    for symbol, pos in list(positions.items()):
        from swing.swing_executor import _get_current_price
        current = _get_current_price(symbol)
        if not current:
            logger.warning(f"[swing_main] 현재가 조회 실패: {symbol}")
            continue

        entry    = pos['entry_price']
        pnl_pct  = (current / entry - 1) * 100

        logger.debug(
            f"[swing_main] 모니터 {pos.get('name', symbol)}({symbol}) | "
            f"진입:{entry:,} 현재:{current:,} "
            f"({pnl_pct:+.2f}%) | {pos['strategy']}"
        )

        exited_flag = check_and_exit(symbol, current)
        if exited_flag:
            exited += 1

        time.sleep(_PRICE_INTERVAL)

    if exited:
        logger.info(f"[swing_main] 모니터 루프: {exited}개 청산")


def _build_candidate_list(watchlist: dict) -> list:
    """
    watchlist를 단일 후보 리스트로 변환 + 스코어 부여
    전략별 우선순위: MOMENTUM > TREND_FOLLOW > REVERSAL
    """
    priority = {'MOMENTUM': 3, 'TREND_FOLLOW': 2, 'REVERSAL': 1}
    result = []

    for strategy, items in watchlist.items():
        if not isinstance(items, list):
            continue
        for item in items:
            # 스코어 = vol_ratio × RSI 정규화 × 전략 우선순위
            vol_ratio = item.get('vol_ratio', 1.0)
            rsi       = item.get('rsi', 50) or 50
            rsi_norm  = abs(rsi - 50) / 50    # 50에서 멀수록 높은 점수
            score     = vol_ratio * (1 + rsi_norm) * priority.get(strategy, 1)

            result.append({
                **item,
                'strategy': strategy,
                'score'   : round(score, 4),
            })

    # 스코어 내림차순 정렬
    result.sort(key=lambda x: x['score'], reverse=True)
    return result


def _shutdown():
    """프로세스 종료 전 정리"""
    logger.info("[swing_main] 종료 처리 시작")
    release_all_by_strategy('SWING')
    logger.info("[swing_main] SWING 락 전체 해제 완료")
    logger.info("[swing_main] === 스윙 전략 종료 ===")


if __name__ == '__main__':
    main()
