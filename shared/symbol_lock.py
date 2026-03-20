"""
shared/symbol_lock.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
종목-전략 충돌 방지 Lock 모듈
- 단타(INTRADAY)와 스윙(SWING)이 동일 종목을 동시에 매수하지 못하도록 통제
- 파일 기반(data/symbol_locks.json) → 별도 프로세스 간 공유 가능
- threading.Lock으로 같은 프로세스 내 스레드 안전성도 보장
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import os
import threading
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 락 파일 경로 (프로젝트 루트 기준)
_LOCK_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'symbol_locks.json')
_LOCK_FILE = os.path.normpath(_LOCK_FILE)

# 프로세스 내 스레드 안전용 뮤텍스
_thread_mutex = threading.Lock()


def _load() -> dict:
    """락 파일 읽기 (없으면 빈 dict 반환)"""
    try:
        if os.path.exists(_LOCK_FILE):
            with open(_LOCK_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[SymbolLock] 락 파일 읽기 실패: {e}")
    return {}


def _save(data: dict):
    """락 파일 저장"""
    os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)
    with open(_LOCK_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def acquire(symbol: str, strategy: str) -> bool:
    """
    종목 Lock 획득 시도
    - 이미 다른 전략이 점유 중이면 False 반환 (획득 실패)
    - 성공 시 True 반환
    
    Args:
        symbol  : 종목코드 (예: "005930")
        strategy: 전략명 "INTRADAY" | "SWING"
    Returns:
        bool: 획득 성공 여부
    """
    with _thread_mutex:
        locks = _load()
        current = locks.get(symbol)

        # 이미 다른 전략이 점유 중
        if current and current['strategy'] != strategy:
            logger.info(
                f"[SymbolLock] 획득 실패 {symbol} | "
                f"점유: {current['strategy']} | 요청: {strategy}"
            )
            return False

        # 동일 전략이 이미 점유 중 (재진입 허용)
        if current and current['strategy'] == strategy:
            return True

        # 신규 획득
        locks[symbol] = {
            'strategy': strategy,
            'acquired_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        _save(locks)
        logger.debug(f"[SymbolLock] 획득 성공 {symbol} → {strategy}")
        return True


def release(symbol: str, strategy: str = None) -> bool:
    """
    종목 Lock 해제
    - strategy를 지정하면 해당 전략이 점유한 경우에만 해제 (안전 모드)
    - strategy=None 이면 무조건 해제 (강제 모드)
    
    Returns:
        bool: 해제 성공 여부
    """
    with _thread_mutex:
        locks = _load()
        current = locks.get(symbol)

        if not current:
            return True  # 이미 해제된 상태

        # 전략 지정 시 소유권 확인
        if strategy and current['strategy'] != strategy:
            logger.warning(
                f"[SymbolLock] 해제 거부 {symbol} | "
                f"점유: {current['strategy']} | 해제 요청: {strategy}"
            )
            return False

        del locks[symbol]
        _save(locks)
        logger.debug(f"[SymbolLock] 해제 완료 {symbol}")
        return True


def is_locked(symbol: str) -> bool:
    """종목이 어떤 전략에든 점유 중인지 확인"""
    locks = _load()
    return symbol in locks


def get_owner(symbol: str) -> str | None:
    """
    종목을 점유 중인 전략명 반환
    Returns:
        str | None: "INTRADAY" | "SWING" | None(미점유)
    """
    locks = _load()
    entry = locks.get(symbol)
    return entry['strategy'] if entry else None


def get_all_locks() -> dict:
    """현재 전체 락 상태 반환 (모니터링용)"""
    return _load()


def release_all_by_strategy(strategy: str) -> int:
    """
    특정 전략의 모든 락 일괄 해제
    - 프로세스 종료 시 정리용
    Returns:
        int: 해제된 종목 수
    """
    with _thread_mutex:
        locks = _load()
        before = len(locks)
        locks = {k: v for k, v in locks.items() if v['strategy'] != strategy}
        after = len(locks)
        _save(locks)
        released = before - after
        if released:
            logger.info(f"[SymbolLock] {strategy} 전략 락 {released}개 일괄 해제")
        return released


def cleanup_stale_locks(max_hours: int = 24):
    """
    오래된 락 자동 정리 (장애 후 재시작 시 사용)
    - max_hours 이상 된 락은 강제 해제
    """
    with _thread_mutex:
        locks = _load()
        now = datetime.now()
        stale = []
        for symbol, info in locks.items():
            try:
                acquired = datetime.strptime(info['acquired_at'], '%Y-%m-%d %H:%M:%S')
                elapsed_hours = (now - acquired).total_seconds() / 3600
                if elapsed_hours > max_hours:
                    stale.append(symbol)
            except Exception:
                stale.append(symbol)  # 파싱 불가 → 오래된 것으로 간주

        for symbol in stale:
            del locks[symbol]
            logger.warning(f"[SymbolLock] 오래된 락 강제 해제: {symbol}")

        if stale:
            _save(locks)
        return stale
