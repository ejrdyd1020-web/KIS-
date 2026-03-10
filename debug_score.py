"""점수 구성요소 상세 확인"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from auth import get_access_token
get_access_token()
from api.ohlcv import load_ohlcv_cache
load_ohlcv_cache()
from api.price import get_volume_rank, get_current_price
from strategy.strategy_breakout import score_breakout

stocks = get_volume_rank(top_n=50)

print(f"  {'종목':<14} {'등락률':>7} {'체결강도':>8} {'거래량순위':>8} {'거래대금순위':>9}  {'점수':>6}")
print("  " + "-" * 70)

for s in stocks[:15]:
    name      = s.get("name", "")
    vol_rank  = s.get("volume_rank", 300)
    trade_rank= s.get("trade_rank", 300)
    change    = s.get("change_rate", 0)

    # 체결강도 별도 조회
    detail = get_current_price(s["code"])
    exec_str = detail.get("exec_strength", 0) if detail else 0
    s["exec_strength"] = exec_str  # score_breakout에서 사용하도록

    total = score_breakout(s)
    print(f"  {name:<14} {change:>+6.1f}%  {exec_str:>7.1f}%  {vol_rank:>6}위  {trade_rank:>7}위  {total:>6.1f}점")
