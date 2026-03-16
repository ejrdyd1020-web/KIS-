# ============================================================
#  utils/logger.py  –  로그 기록 유틸리티
# ============================================================

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    """
    파일 + 콘솔 동시 출력 로거 반환.
    로그 파일: logs/YYYY-MM-DD.log (자정에 자동 교체, 30일 보관)
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # 이미 설정된 경우 재사용

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 콘솔 핸들러 (utf-8 강제 설정으로 이모지 출력 가능)
    ch = logging.StreamHandler(stream=open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, closefd=False))
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)

    # 파일 핸들러 — 자정에 자동 교체, 파일명: YYYY-MM-DD.log
    # TimedRotatingFileHandler: 기본파일 autotrader.log → 교체 시 날짜 suffix 추가
    log_file = os.path.join(LOG_DIR, "autotrader.log")
    fh = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    # 교체된 파일명을 YYYY-MM-DD.log 형식으로 변환
    fh.suffix  = "%Y-%m-%d"
    fh.namer   = lambda name: name.replace("autotrader.log.", "") + ".log"
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)

    return logger
