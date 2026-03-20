"""
backtest_runner.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
백테스트 실행기
- config.BASE_URL, config.ACCESS_TOKEN 참조 제거 (기존 버그 수정)
- auth.get_headers() / auth.get_base_url() 패턴으로 통일
- KIS_IS_REAL=true 환경변수로 실서버 전환 (목서버는 일봉 API 미지원)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

실행 방법:
    # 실서버 기준 백테스트 (일봉 API 사용)
    $env:KIS_IS_REAL="true"
    python backtest_runner.py --strategy breakout --start 20250101 --end 20251231
    python backtest_runner.py --strategy reversion
    python backtest_runner.py --strategy swing_momentum
"""

import os
import sys
import argparse
import logging
from datetime import datetime

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── auth 패턴 임포트 (config.BASE_URL/ACCESS_TOKEN 사용 금지) ──
from auth import get_headers, get_base_url, get_access_token

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/backtest.log', encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='KIS AutoTrader 백테스트')
    parser.add_argument('--strategy', choices=['breakout', 'reversion', 'swing_momentum', 'swing_reversal', 'swing_trend'],
                        default='breakout', help='백테스트 전략')
    parser.add_argument('--start', default='20250101', help='시작일 YYYYMMDD')
    parser.add_argument('--end',   default=datetime.now().strftime('%Y%m%d'), help='종료일 YYYYMMDD')
    parser.add_argument('--capital', type=int, default=10_000_000, help='초기 자본 (원)')
    parser.add_argument('--symbols', nargs='*', help='테스트 종목 목록 (미입력시 자동 선정)')
    args = parser.parse_args()

    # ── 실서버 여부 확인 ──────────────────────────────────────
    is_real = os.getenv('KIS_IS_REAL', 'false').lower() == 'true'
    base_url = get_base_url()  # ← 올바른 패턴 (config.BASE_URL 금지)

    logger.info("=" * 60)
    logger.info(f"백테스트 시작: {args.strategy.upper()}")
    logger.info(f"기간: {args.start} ~ {args.end}")
    logger.info(f"초기자본: {args.capital:,}원")
    logger.info(f"서버: {'실서버' if is_real else '모의서버'}")
    logger.info(f"API URL: {base_url}")
    logger.info("=" * 60)

    if not is_real:
        logger.warning(
            "⚠️  모의서버는 일봉 OHLCV API를 지원하지 않습니다.\n"
            "   스윙 전략 백테스트는 실서버 필요:\n"
            "   PowerShell: $env:KIS_IS_REAL='true'\n"
            "   CMD:        set KIS_IS_REAL=true"
        )
        if args.strategy.startswith('swing'):
            logger.error("스윙 전략 백테스트는 실서버(KIS_IS_REAL=true) 필요. 종료.")
            sys.exit(1)

    # ── 액세스 토큰 발급 확인 ────────────────────────────────
    try:
        token = get_access_token()  # ← 올바른 패턴 (config.ACCESS_TOKEN 금지)
        if not token:
            raise ValueError("액세스 토큰 발급 실패")
        logger.info(f"액세스 토큰 발급 성공: {token[:10]}...")
    except Exception as e:
        logger.error(f"인증 실패: {e}")
        sys.exit(1)

    # ── 전략별 백테스트 엔진 실행 ────────────────────────────
    os.makedirs('logs', exist_ok=True)

    if args.strategy == 'breakout':
        from backtest_breakout import BacktestBreakout
        engine = BacktestBreakout(
            start_date=args.start,
            end_date=args.end,
            initial_capital=args.capital,
            symbols=args.symbols,
        )
    elif args.strategy == 'reversion':
        from backtest_reversion import BacktestReversion
        engine = BacktestReversion(
            start_date=args.start,
            end_date=args.end,
            initial_capital=args.capital,
            symbols=args.symbols,
        )
    elif args.strategy.startswith('swing'):
        strategy_map = {
            'swing_momentum': 'MOMENTUM',
            'swing_reversal': 'REVERSAL',
            'swing_trend'   : 'TREND_FOLLOW',
        }
        from swing.backtest_swing import BacktestSwing
        engine = BacktestSwing(
            strategy_type=strategy_map[args.strategy],
            start_date=args.start,
            end_date=args.end,
            initial_capital=args.capital,
            symbols=args.symbols,
        )
    else:
        logger.error(f"알 수 없는 전략: {args.strategy}")
        sys.exit(1)

    # ── 실행 및 결과 출력 ────────────────────────────────────
    result = engine.run()

    if result:
        print("\n" + "=" * 60)
        print(f"📊 백테스트 결과: {args.strategy.upper()}")
        print("=" * 60)
        print(f"  기간          : {args.start} ~ {args.end}")
        print(f"  초기 자본     : {result.get('initial_capital', 0):>15,.0f} 원")
        print(f"  최종 자산     : {result.get('final_capital', 0):>15,.0f} 원")
        print(f"  총 수익       : {result.get('total_profit', 0):>+15,.0f} 원")
        print(f"  수익률        : {result.get('return_pct', 0):>+14.2f} %")
        print(f"  총 거래 수    : {result.get('total_trades', 0):>15,} 건")
        print(f"  승률          : {result.get('win_rate', 0):>14.1f} %")
        print(f"  최대 낙폭(MDD): {result.get('mdd', 0):>+14.2f} %")
        print(f"  샤프 비율     : {result.get('sharpe', 0):>14.3f}")
        print("=" * 60)

        # 결과 파일 저장
        import json
        result_file = f"logs/backtest_{args.strategy}_{args.start}_{args.end}.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"결과 저장: {result_file}")


if __name__ == '__main__':
    main()
