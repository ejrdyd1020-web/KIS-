"""
backtest_reversion.py
REVERSION 전략 백테스트
- 스토캐스틱 슬로우 골든크로스 (과매도 구간)
- MA120 상승장 필터
- TP: +3% / SL: -2.5%
- 09:10~15:20 시간대
"""
import pandas as pd
from backtest_engine import BacktestEngine


# ─────────────────────────────────────────
# 파라미터 (strategy_reversion.py와 동일)
# ─────────────────────────────────────────
REVERSION_START = "09:10"
REVERSION_END = "15:20"
STOCH_K_PERIOD = 14
STOCH_D_PERIOD = 3
STOCH_OVERSOLD = 20
MA120_PERIOD = 120
TAKE_PROFIT = 0.03       # +3%
STOP_LOSS = 0.025        # -2.5%


# ─────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────
def _stochastic_slow(df: pd.DataFrame, k=14, d=3) -> pd.DataFrame:
    low_min = df["low"].rolling(k).min()
    high_max = df["high"].rolling(k).max()
    fast_k = (df["close"] - low_min) / (high_max - low_min + 1e-9) * 100
    slow_k = fast_k.rolling(d).mean()
    slow_d = slow_k.rolling(d).mean()
    return slow_k, slow_d


def _ma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def _is_bull_market(close_series: pd.Series) -> bool:
    """MA120 기준 상승장 판단"""
    ma120 = _ma(close_series, MA120_PERIOD)
    if ma120.isna().iloc[-1]:
        return True  # 데이터 부족 시 허용
    return float(close_series.iloc[-1]) >= float(ma120.iloc[-1])


# ─────────────────────────────────────────
# 백테스트 실행
# ─────────────────────────────────────────
def run_reversion_backtest(
    engine: BacktestEngine,
    codes: list[str],
    trade_dates: list[str],
    daily_data: dict[str, pd.DataFrame],
    minute_data: dict[str, dict[str, pd.DataFrame]],
    budget_per_slot: float,
) -> list[dict]:
    """
    REVERSION 전략 백테스트 실행
    Returns: 체결된 trade 리스트
    """
    all_trades = []

    for date in trade_dates:
        for code in codes:
            daily_df = daily_data.get(code, pd.DataFrame())
            min_df = minute_data.get(code, {}).get(date, pd.DataFrame())

            if daily_df.empty or min_df.empty:
                continue

            # MA120 상승장 필터
            if not _is_bull_market(daily_df["close"]):
                continue

            # 09:10~15:20 구간 분봉
            window = min_df[
                (min_df["time"].dt.strftime("%H:%M") >= REVERSION_START) &
                (min_df["time"].dt.strftime("%H:%M") <= REVERSION_END)
            ].reset_index(drop=True)

            if len(window) < STOCH_K_PERIOD + STOCH_D_PERIOD:
                continue

            slow_k, slow_d = _stochastic_slow(window)

            already_entered = code in engine.positions

            for i in range(1, len(window)):
                cur_time = str(window.iloc[i]["time"])
                cur_price = float(window.iloc[i]["close"])

                # 포지션 없을 때 진입 신호 탐색
                if not already_entered and engine.available_slots > 0:
                    prev_k = slow_k.iloc[i - 1]
                    prev_d = slow_d.iloc[i - 1]
                    cur_k = slow_k.iloc[i]
                    cur_d = slow_d.iloc[i]

                    if pd.isna(prev_k) or pd.isna(prev_d):
                        continue

                    # 골든크로스 + 과매도 구간
                    golden_cross = (prev_k < prev_d) and (cur_k >= cur_d)
                    oversold = cur_k < STOCH_OVERSOLD

                    if golden_cross and oversold:
                        opened = engine.open_position(
                            code=code,
                            strategy="REVERSION",
                            price=cur_price,
                            budget=budget_per_slot,
                            stop_loss_pct=STOP_LOSS,
                            take_profit_pct=TAKE_PROFIT,
                            entry_time=cur_time,
                        )
                        if opened:
                            already_entered = True
                            print(f"  [REVERSION 진입] {code} {cur_time} @{cur_price:,} K={cur_k:.1f}")

                # 포지션 있을 때 청산 체크
                if already_entered and code in engine.positions:
                    closed = engine.check_exits({code: cur_price}, cur_time)
                    if closed:
                        all_trades.extend(closed)
                        already_entered = False
                        for t in closed:
                            print(f"  [REVERSION 청산] {code} {t['reason']} pnl={t['pnl']:,}원 ({t['pnl_pct']}%)")

            # 장 마감 강제 청산
            if code in engine.positions and engine.positions[code].strategy == "REVERSION":
                last_price = float(window.iloc[-1]["close"]) if not window.empty else engine.positions[code].entry_price
                trade = engine.close_position(code, last_price, f"{date} 15:20:00", "EOD")
                if trade:
                    all_trades.append(trade)
                    print(f"  [REVERSION 청산] {code} 장마감 pnl={trade['pnl']:,}원 ({trade['pnl_pct']}%)")

    return all_trades
