"""
스토캐스틱 실제 값 확인
실행: python debug_stoch.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from auth import get_access_token
get_access_token()

from api.ohlcv import load_ohlcv_cache
load_ohlcv_cache()

from api.chart import get_minute_chart_bulk
from strategy.strategy_reversion import _calc_stochastic_slow, check_stochastic_signal
import pandas as pd

CODES = [
    ("279570", "케이뱅크"),
    ("005930", "삼성전자"),
    ("006910", "보성파워텍"),
    ("032820", "우리기술"),
    ("0163Y0", "KoAct"),
]

print(f"{'종목':<15} {'slow_k':>8} {'slow_d':>8} {'prev_k':>8} {'prev_d':>8}  {'신호'}")
print("-" * 65)

for code, name in CODES:
    candles = get_minute_chart_bulk(code, need=100)
    if len(candles) < 25:
        print(f"{name:<15} 데이터부족({len(candles)}개)")
        continue
    df = pd.DataFrame(candles).iloc[::-1].reset_index(drop=True)
    slow_k, slow_d = _calc_stochastic_slow(df)
    lk  = slow_k.iloc[-1]
    ld  = slow_d.iloc[-1]
    pk  = slow_k.iloc[-2]
    pd_ = slow_d.iloc[-2]

    if any(pd.isna(v) for v in [lk, ld, pk, pd_]):
        sig = "NaN"
    elif lk > ld and pk <= pd_ and min(pk, pd_) <= 20:
        sig = "🟢 BUY"
    elif lk < ld and pk >= pd_ and max(pk, pd_) >= 80:
        sig = "🔴 SELL"
    else:
        sig = "⚪ HOLD"

    print(f"{name:<15} {lk:>8.1f} {ld:>8.1f} {pk:>8.1f} {pd_:>8.1f}  {sig}")
    print(f"  → BUY조건: slow_k({lk:.1f})>slow_d({ld:.1f}) AND prev_k({pk:.1f})<=prev_d({pd_:.1f}) AND min({min(pk,pd_):.1f})<=20")
