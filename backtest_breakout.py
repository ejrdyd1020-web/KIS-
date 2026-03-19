"""
backtest_breakout.py
BREAKOUT 전략 백테스트
- 전일 고가 돌파 + 5x 거래량 급증
- TP: +5% / SL: -3% / Trailing Stop: 2%
- 09:00~09:10 시간대 신호 탐색
"""
import pandas as pd
from backtest_engine import BacktestEngine


# ─────────────────────────────────────────
# 파라미터 (strategy_breakout.py와 동일)
# ─────────────────────────────────────────
BREAKOUT_START = "09:00"
BREAKOUT_END = "09:10"
VOLUME_SURGE = 5.0       # 5배 거래량 급증
TAKE_PROFIT = 0.05       # +5%
STOP_LOSS = 0.03         # -3%
TRAILING_STOP = 0.02     # 2% trailing


def _prev_day_high(daily_df: pd.DataFrame, trade_date: str) -> float:
    """전일 고가 반환"""
    date = pd.to_datetime(trade_date, format="%Y%m%d")
    prev = daily_df[daily_df["date"] < date]
    if prev.empty:
        return float("inf")
    return float(prev.iloc[-1]["high"])


def _avg_volume(daily_df: pd.DataFrame, trade_date: str, days: int = 5) -> float:
    """직전 N일 평균 거래량"""
    date = pd.to_datetime(trade_date, format="%Y%m%d")
    prev = daily_df[daily_df["date"] < date].tail(days)
    if prev.empty:
        return 0
    return float(prev["volume"].mean())


def run_breakout_backtest(
    engine: BacktestEngine,
    codes: list[str],
    trade_dates: list[str],
    daily_data: dict[str, pd.DataFrame],    # code → 일봉 DataFrame
    minute_data: dict[str, dict[str, pd.DataFrame]],  # code → date → 분봉 DataFrame
    budget_per_slot: float,
) -> list[dict]:
    """
    BREAKOUT 전략 백테스트 실행
    Returns: 체결된 trade 리스트
    """
    all_trades = []

    for date in trade_dates:
        # 09:00~09:10 신호 스캔
        candidates = []

        for code in codes:
            daily_df = daily_data.get(code, pd.DataFrame())
            if daily_df.empty:
                continue

            min_df = minute_data.get(code, {}).get(date, pd.DataFrame())
            if min_df.empty:
                continue

            prev_high = _prev_day_high(daily_df, date)
            avg_vol = _avg_volume(daily_df, date)
            if avg_vol == 0:
                continue

            # 09:00~09:10 구간 분봉 필터
            window = min_df[
                (min_df["time"].dt.strftime("%H:%M") >= BREAKOUT_START) &
                (min_df["time"].dt.strftime("%H:%M") <= BREAKOUT_END)
            ]
            if window.empty:
                continue

            for _, row in window.iterrows():
                if (row["high"] > prev_high and
                        row["volume"] >= avg_vol * VOLUME_SURGE):
                    score = (row["close"] / prev_high - 1) * (row["volume"] / avg_vol)
                    candidates.append({
                        "code": code,
                        "price": row["close"],
                        "time": str(row["time"]),
                        "score": score,
                    })
                    break  # 종목당 1번만

        # 점수 기준 상위 3개 선택
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top3 = candidates[:3]

        if not top3:
            continue

        total_score = sum(c["score"] for c in top3)

        for c in top3:
            if engine.available_slots <= 0:
                break
            weight = c["score"] / total_score
            budget = budget_per_slot * weight

            opened = engine.open_position(
                code=c["code"],
                strategy="BREAKOUT",
                price=c["price"],
                budget=budget,
                stop_loss_pct=STOP_LOSS,
                take_profit_pct=TAKE_PROFIT,
                entry_time=c["time"],
                trailing_stop_pct=TRAILING_STOP,
            )
            if opened:
                print(f"  [BREAKOUT 진입] {c['code']} {c['time']} @{c['price']:,} score={c['score']:.3f}")

        # 당일 분봉으로 청산 체크
        for code in list(engine.positions.keys()):
            if engine.positions[code].strategy != "BREAKOUT":
                continue
            min_df = minute_data.get(code, {}).get(date, pd.DataFrame())
            if min_df.empty:
                continue

            for _, row in min_df.iterrows():
                price_map = {code: row["close"]}
                closed = engine.check_exits(price_map, str(row["time"]))
                all_trades.extend(closed)

        # 장 마감 강제 청산 (15:20)
        for code in list(engine.positions.keys()):
            if engine.positions[code].strategy != "BREAKOUT":
                continue
            min_df = minute_data.get(code, {}).get(date, pd.DataFrame())
            last_price = (
                float(min_df.iloc[-1]["close"]) if not min_df.empty
                else engine.positions[code].entry_price
            )
            trade = engine.close_position(code, last_price, f"{date} 15:20:00", "EOD")
            if trade:
                all_trades.append(trade)
                print(f"  [BREAKOUT 청산] {code} 장마감 pnl={trade['pnl']:,}원 ({trade['pnl_pct']}%)")

    return all_trades
