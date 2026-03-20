"""
premarket.py  (수정본)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
장전 준비 작업 (08:50 실행)
- 기존: 단타 watchlist.json 생성 (변경 없음)
- 추가: --mode swing 옵션 시 스윙 watchlist_swing.json 생성

Task Scheduler 등록:
  단타: python premarket.py           → watchlist.json
  스윙: python premarket.py --mode swing → watchlist_swing.json
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            f"logs/premarket_{datetime.now().strftime('%Y%m%d')}.log",
            encoding='utf-8'
        ),
    ]
)
logger = logging.getLogger('premarket')


def run_intraday_premarket():
    """
    단타 장전 준비 (기존 로직 그대로 유지)
    - KOSPI200 구성종목 + 거래대금 상위 스캔
    - data/watchlist.json 저장
    """
    logger.info("[Premarket] 단타 장전 스캔 시작")
    # ── 기존 단타 premarket 로직 유지 ─────────────────────────
    # (기존 코드를 여기에 그대로 유지, 스윙 코드와 혼합하지 않음)
    from condition import filter_breakout_candidates, filter_reversion_candidates
    import json

    breakout  = filter_breakout_candidates()
    reversion = filter_reversion_candidates()

    watchlist = {
        'BREAKOUT' : breakout  or [],
        'REVERSION': reversion or [],
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    os.makedirs('data', exist_ok=True)
    with open('data/watchlist.json', 'w', encoding='utf-8') as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)

    logger.info(
        f"[Premarket] 단타 완료 | "
        f"BREAKOUT:{len(watchlist['BREAKOUT'])} "
        f"REVERSION:{len(watchlist['REVERSION'])}"
    )


def run_swing_premarket():
    """
    스윙 장전 준비 (신규)
    - 일봉 기반 3전략 후보 스캔
    - data/watchlist_swing.json 저장 (단타 파일과 완전 분리)
    """
    logger.info("[Premarket] 스윙 장전 스캔 시작")
    from swing.swing_scanner import run_scan
    result = run_scan()
    logger.info(
        f"[Premarket] 스윙 완료 | "
        f"MOM:{len(result.get('MOMENTUM',[]))} "
        f"REV:{len(result.get('REVERSAL',[]))} "
        f"TRF:{len(result.get('TREND_FOLLOW',[]))}"
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--mode',
        choices=['intraday', 'swing'],
        default='intraday',
        help='intraday: 단타 watchlist 생성 (기본) | swing: 스윙 watchlist 생성'
    )
    args = parser.parse_args()

    os.makedirs('logs', exist_ok=True)
    os.makedirs('data', exist_ok=True)

    if args.mode == 'swing':
        run_swing_premarket()
    else:
        run_intraday_premarket()
