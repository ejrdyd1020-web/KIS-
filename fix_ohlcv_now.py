# ============================================================
#  fix_ohlcv_now.py  —  ohlcv 캐시 즉시 수집 (장 중 수동 실행용)
#
#  실행:  python fix_ohlcv_now.py
# ============================================================
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from auth import get_access_token
get_access_token()

from api.price import get_volume_rank
from premarket import get_daily_chart, _save_to_ohlcv_cache
from utils.logger import get_logger

logger = get_logger("fix_ohlcv")

print("=" * 55)
print("  📦 ohlcv 캐시 즉시 수집 시작")
print("=" * 55)

# 거래량 순위 상위 50개 조회
stocks = get_volume_rank(top_n=50)
if not stocks:
    print("❌ 거래량 순위 조회 실패")
    sys.exit(1)

print(f"  {len(stocks)}개 종목 대상으로 일봉 수집 중...\n")

success = 0
for s in stocks:
    code = s["code"]
    name = s["name"]
    candles = get_daily_chart(code, count=30)
    if candles:
        _save_to_ohlcv_cache(code, candles)
        print(f"  ✅ [{name}] 전일고가: {candles[0]['high']:,}원 | 전일거래대금: {int(candles[0].get('trade_amount',0)/100_000_000):,}억")
        success += 1
    else:
        print(f"  ⚠ [{name}] 일봉 조회 실패")
    time.sleep(0.3)

print(f"\n  총 {success}개 종목 ohlcv 캐시 저장 완료")
print("  이제 test_breakout_live.py 를 다시 실행하세요!")
