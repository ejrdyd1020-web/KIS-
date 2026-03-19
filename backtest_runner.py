"""
backtest_runner.py
백테스트 메인 실행 파일

사용법:
    python backtest_runner.py
"""
import json
from datetime import datetime, timedelta
from collections import defaultdict

import config
from data_loader import (
    get_kospi200_codes,
    get_daily_ohlcv,
    get_minute_ohlcv,
    get_trading_days,
)
from backtest_engine import BacktestEngine
from backtest_breakout import run_breakout_backtest
from backtest_reversion import run_reversion_backtest


# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
INITIAL_CAPITAL  = config.TOTAL_BUDGET
MAX_POSITIONS    = config.MAX_POSITIONS
MINUTE_INTERVAL  = 5

BREAKOUT_RATIO   = 0.5
REVERSION_RATIO  = 0.5


def get_date_range():
    end = datetime.now()
    start = end - timedelta(days=30)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


# ─────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────
def load_all_data(codes, start_date, end_date, trade_dates):
    print(f"\n[데이터 로드] {len(codes)}종목 / {start_date}~{end_date}")

    daily_data = {}
    minute_data = defaultdict(dict)

    for i, code in enumerate(codes):
        if i % 50 == 0:
            print(f"  일봉 로딩 {i}/{len(codes)}...")
        ext_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
        df = get_daily_ohlcv(code, ext_start, end_date)
        if not df.empty:
            daily_data[code] = df

    print(f"  일봉 완료: {len(daily_data)}종목")

    total = len(codes) * len(trade_dates)
    done = 0
    for code in codes:
        for date in trade_dates:
            df = get_minute_ohlcv(code, date, MINUTE_INTERVAL)
            if not df.empty:
                minute_data[code][date] = df
            done += 1
            if done % 200 == 0:
                print(f"  분봉 로딩 {done}/{total}...")

    print("  분봉 완료")
    return daily_data, minute_data


# ─────────────────────────────────────────
# 결과 출력
# ─────────────────────────────────────────
def print_summary(label, summary, trades):
    print(f"\n{'='*50}")
    print(f"  {label} 백테스트 결과")
    print(f"{'='*50}")
    print(f"  초기자본     : {summary.get('initial_capital', 0):>15,.0f} 원")
    print(f"  최종자산     : {summary.get('final_equity', 0):>15,.0f} 원")
    print(f"  총수익률     : {summary.get('total_return_pct', 0):>14.2f} %")
    print(f"  총손익       : {summary.get('total_pnl', 0):>15,.0f} 원")
    print(f"  총 거래수    : {summary.get('total_trades', 0):>15} 건")
    print(f"  승률         : {summary.get('win_rate_pct', 0):>14.1f} %")
    print(f"  평균 수익    : {summary.get('avg_win', 0):>15,.0f} 원")
    print(f"  평균 손실    : {summary.get('avg_loss', 0):>15,.0f} 원")
    print(f"  Profit Factor: {summary.get('profit_factor', 0):>14.2f}")
    print(f"  MDD          : {summary.get('mdd_pct', 0):>14.2f} %")

    sell_trades = [t for t in trades if t["side"] == "SELL" and t.get("pnl") is not None]
    if sell_trades:
        sell_trades.sort(key=lambda x: x["pnl"], reverse=True)
        print(f"\n  ▲ 수익 TOP3")
        for t in sell_trades[:3]:
            print(f"    {t['code']} {t['pnl_pct']:+.2f}% ({t['pnl']:+,.0f}원) [{t.get('reason','')}]")
        print(f"\n  ▼ 손실 TOP3")
        for t in sell_trades[-3:]:
            print(f"    {t['code']} {t['pnl_pct']:+.2f}% ({t['pnl']:+,.0f}원) [{t.get('reason','')}]")


def save_trades_csv(trades, filename):
    import csv
    if not trades:
        return
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=trades[0].keys())
        writer.writeheader()
        writer.writerows(trades)
    print(f"  체결내역 저장: {filename}")


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
def main():
    start_date, end_date = get_date_range()
    trade_dates = get_trading_days(start_date, end_date)
    print(f"\n[백테스트 시작]")
    print(f"  기간: {start_date} ~ {end_date} ({len(trade_dates)}거래일)")
    print(f"  초기자본: {INITIAL_CAPITAL:,}원 / 최대포지션: {MAX_POSITIONS}")

    codes = get_kospi200_codes()
    if not codes:
        print("[오류] KOSPI200 종목 조회 실패")
        return

    daily_data, minute_data = load_all_data(codes, start_date, end_date, trade_dates)

    # BREAKOUT
    print(f"\n[BREAKOUT 전략 백테스트 시작]")
    engine_b = BacktestEngine(INITIAL_CAPITAL * BREAKOUT_RATIO, MAX_POSITIONS // 2)
    budget_b = (INITIAL_CAPITAL * BREAKOUT_RATIO) / (MAX_POSITIONS // 2)
    run_breakout_backtest(engine_b, codes, trade_dates, daily_data, minute_data, budget_b)
    summary_b = engine_b.summary()
    print_summary("BREAKOUT", summary_b, engine_b.trades)
    save_trades_csv(engine_b.trades, "results_breakout.csv")

    # REVERSION
    print(f"\n[REVERSION 전략 백테스트 시작]")
    engine_r = BacktestEngine(INITIAL_CAPITAL * REVERSION_RATIO, MAX_POSITIONS // 2)
    budget_r = (INITIAL_CAPITAL * REVERSION_RATIO) / (MAX_POSITIONS // 2)
    run_reversion_backtest(engine_r, codes, trade_dates, daily_data, minute_data, budget_r)
    summary_r = engine_r.summary()
    print_summary("REVERSION", summary_r, engine_r.trades)
    save_trades_csv(engine_r.trades, "results_reversion.csv")

    # 종합
    total_final = (
        summary_b.get("final_equity", INITIAL_CAPITAL * BREAKOUT_RATIO) +
        summary_r.get("final_equity", INITIAL_CAPITAL * REVERSION_RATIO)
    )
    total_return = (total_final - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    print(f"\n{'='*50}")
    print(f"  [종합] 최종자산: {total_final:,.0f}원 / 수익률: {total_return:+.2f}%")
    print(f"{'='*50}\n")

    with open("result_summary.json", "w", encoding="utf-8") as f:
        json.dump({
            "period": f"{start_date}~{end_date}",
            "BREAKOUT": summary_b,
            "REVERSION": summary_r,
            "combined_return_pct": round(total_return, 2),
        }, f, ensure_ascii=False, indent=2)
    print("  결과 저장: result_summary.json")


if __name__ == "__main__":
    main()
