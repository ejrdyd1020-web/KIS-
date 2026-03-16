# ============================================================
#  backtest_reversion.py  –  REVERSION 전략 다중일 백테스트
#
#  전략 요약:
#    진입: 1분봉 스토캐스틱(K<20) 골든크로스 발생 시 시장가 매수
#    청산: 고정손절(-1.5%) | 고정익절(+3%) | 트레일링(+2% 이상→고점-1.5%) | 장마감
#
#  실행:
#    python backtest_reversion.py
#    python backtest_reversion.py --days 20   # 최근 20 영업일
#    python backtest_reversion.py --top 50    # 거래량 상위 50개 종목
# ============================================================

import sys
import os
import time
import argparse
from datetime import date, datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import requests
import pandas as pd
from auth import get_access_token, get_headers, get_base_url
from strategy.indicators import calc_stochastic_slow

# ── 전략 파라미터 (config.py REVERSION 기준) ───────────────
STOP_LOSS_PCT    = -1.5   # 고정 손절 (%)
TAKE_PROFIT_PCT  =  3.0   # 고정 익절 (%)
TRAIL_MIN_PCT    =  2.0   # 트레일링 최소 수익 (%)
TRAIL_DROP_PCT   =  1.5   # 고점 대비 하락 허용 (%)
STOCH_K_PERIOD   = 12
STOCH_SMOOTH     =  5
STOCH_D_PERIOD   =  5
CROSS_WINDOW     =  3     # 골든크로스 감지 봉 수
FORCE_SELL_TIME  = "1520"
BUY_START_TIME   = "0910"

# 거래 비용
BUY_FEE  = 0.00015
SELL_FEE  = 0.00015
SELL_TAX  = 0.0018

BASE_URL = get_base_url()


# ══════════════════════════════════════════
# KIS API – 분봉 데이터 수집
# ══════════════════════════════════════════

def fetch_minute_candles(stock_code: str, target_date: str, delay: float = 0.2) -> list[dict]:
    """
    특정 날짜의 1분봉 전체 수집 (역방향 반복 호출).

    target_date: "20260314" 형식
    Returns: [{time, open, high, low, close, volume}, ...] 시간 오름차순
    """
    all_candles = []
    seen_times  = set()
    end_time    = "160000"   # 15:30까지 역방향 조회

    for _ in range(15):   # 최대 15회 (1회당 ~30개, 450분 커버)
        params = {
            "fid_etc_cls_code"      : "",
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd"        : stock_code,
            "fid_input_hour_1"      : end_time,
            "fid_pw_data_incu_yn"   : "Y",
        }
        try:
            res = requests.get(
                f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                headers=get_headers("FHKST03010200"),
                params=params,
                timeout=10,
            )
            data = res.json()
        except Exception as e:
            print(f"  [오류] {stock_code} 분봉 조회 실패: {e}")
            break

        if data.get("rt_cd") != "0":
            break

        items = data.get("output2", [])
        if not items:
            break

        new_added = False
        for c in items:
            if c.get("stck_bsop_date", "") != target_date:
                continue
            t = c.get("stck_cntg_hour", "")
            if not t or t in seen_times:
                continue
            seen_times.add(t)
            all_candles.append({
                "time"  : t,
                "open"  : int(c.get("stck_oprc", 0)),
                "high"  : int(c.get("stck_hgpr", 0)),
                "low"   : int(c.get("stck_lwpr", 0)),
                "close" : int(c.get("stck_prpr", 0)),
                "volume": int(c.get("cntg_vol", 0)),
            })
            new_added = True

        if not new_added:
            break

        last_t = items[-1].get("stck_cntg_hour", "")
        if not last_t:
            break
        end_time = last_t
        time.sleep(delay)

    all_candles.sort(key=lambda x: x["time"])
    return all_candles


def fetch_volume_top(top_n: int = 30, change_min: float = 3.0, change_max: float = 15.0) -> list[dict]:
    """당일 거래량 상위 종목 조회 (REVERSION 등락률 범위 필터 적용)"""
    try:
        res = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=get_headers("FHPST01710000"),
            params={
                "fid_cond_mrkt_div_code" : "J",
                "fid_cond_scr_div_code"  : "20171",
                "fid_input_iscd"         : "0000",
                "fid_rank_sort_cls_code" : "0",
                "fid_input_cnt_1"        : "0",
                "fid_prc_cls_code"       : "0",
                "fid_input_price_1"      : "",
                "fid_input_price_2"      : "",
                "fid_vol_cnt"            : "",
                "fid_trgt_cls_code"      : "111111111",
                "fid_trgt_exls_cls_code" : "000000",
                "fid_div_cls_code"       : "0",
            },
            timeout=10,
        )
        data = res.json()
        results = []
        for item in data.get("output", [])[:top_n]:
            cr = float(item.get("prdy_ctrt", 0))
            if not (change_min <= cr <= change_max):
                continue
            results.append({
                "code": item.get("mksc_shrn_iscd", ""),
                "name": item.get("hts_kor_isnm", ""),
                "change_rate": cr,
            })
        return results
    except Exception as e:
        print(f"  [오류] 거래량 순위 조회 실패: {e}")
        return []


# ══════════════════════════════════════════
# 스토캐스틱 신호 감지
# ══════════════════════════════════════════

def detect_stoch_buy(candles: list[dict], idx: int) -> bool:
    """
    idx봉 기준으로 최근 CROSS_WINDOW봉 이내 골든크로스(K<20) 발생 여부.
    slow_k[idx] < 40 조건도 확인 (이미 많이 반등한 경우 제외).
    """
    need = STOCH_K_PERIOD + STOCH_SMOOTH + STOCH_D_PERIOD + CROSS_WINDOW + 5
    if idx < need:
        return False

    window = candles[max(0, idx - 150): idx + 1]
    df = pd.DataFrame(window)
    slow_k, slow_d = calc_stochastic_slow(df, k_period=STOCH_K_PERIOD,
                                          smooth_period=STOCH_SMOOTH,
                                          d_period=STOCH_D_PERIOD)

    if len(slow_k) < CROSS_WINDOW + 2:
        return False

    cur_k = slow_k.iloc[-1]
    if pd.isna(cur_k) or cur_k >= 40:
        return False

    for i in range(-1, -(CROSS_WINDOW + 1), -1):
        ck  = slow_k.iloc[i];   cd  = slow_d.iloc[i]
        pk  = slow_k.iloc[i-1]; pd_ = slow_d.iloc[i-1]
        if any(pd.isna(v) for v in [ck, cd, pk, pd_]):
            continue
        if ck > cd and pk <= pd_ and min(pk, pd_) <= 20:
            return True
    return False


# ══════════════════════════════════════════
# 단일 종목 단일 날짜 백테스트
# ══════════════════════════════════════════

def backtest_single(code: str, name: str, candles: list[dict]) -> list[dict]:
    """
    하나의 분봉 리스트에서 REVERSION 전략 시뮬레이션.
    진입 여러 번 가능 (재매수 허용).
    Returns: 매매 결과 리스트
    """
    results = []
    in_position = False
    buy_price = max_price = stop = tp = 0
    buy_time  = ""

    for idx, c in enumerate(candles):
        t     = c["time"]
        price = c["close"]

        if t < BUY_START_TIME:
            continue

        if in_position:
            if price > max_price:
                max_price = price

            pnl_pct = (price - buy_price) / buy_price * 100

            # 1. 고정 손절
            if price <= stop:
                net_pnl = _calc_net_pnl(buy_price, price)
                results.append({
                    "buy_time" : buy_time, "sell_time": t,
                    "buy_price": buy_price, "sell_price": price,
                    "reason"   : "고정손절",
                    "pnl_pct"  : pnl_pct, "net_pnl": net_pnl,
                })
                in_position = False
                continue

            # 2. 고정 익절
            if price >= tp:
                net_pnl = _calc_net_pnl(buy_price, price)
                results.append({
                    "buy_time" : buy_time, "sell_time": t,
                    "buy_price": buy_price, "sell_price": price,
                    "reason"   : "고정익절",
                    "pnl_pct"  : pnl_pct, "net_pnl": net_pnl,
                })
                in_position = False
                continue

            # 3. 트레일링 스탑
            if pnl_pct >= TRAIL_MIN_PCT:
                drop = (max_price - price) / max_price * 100
                if drop >= TRAIL_DROP_PCT:
                    net_pnl = _calc_net_pnl(buy_price, price)
                    results.append({
                        "buy_time" : buy_time, "sell_time": t,
                        "buy_price": buy_price, "sell_price": price,
                        "reason"   : f"트레일링({pnl_pct:.1f}%→-{drop:.1f}%)",
                        "pnl_pct"  : pnl_pct, "net_pnl": net_pnl,
                    })
                    in_position = False
                    continue

            # 4. 장마감 강제청산
            if t >= FORCE_SELL_TIME:
                net_pnl = _calc_net_pnl(buy_price, price)
                results.append({
                    "buy_time" : buy_time, "sell_time": t,
                    "buy_price": buy_price, "sell_price": price,
                    "reason"   : "장마감",
                    "pnl_pct"  : pnl_pct, "net_pnl": net_pnl,
                })
                in_position = False
                break

        else:
            # 매수 신호 감지 (스토캐스틱 골든크로스)
            if detect_stoch_buy(candles, idx):
                buy_price = price
                max_price = price
                stop      = int(buy_price * (1 + STOP_LOSS_PCT   / 100))
                tp        = int(buy_price * (1 + TAKE_PROFIT_PCT / 100))
                buy_time  = t
                in_position = True

    # 미청산 포지션 → 마지막 가격으로 강제청산
    if in_position and candles:
        last = candles[-1]
        pnl_pct = (last["close"] - buy_price) / buy_price * 100
        results.append({
            "buy_time" : buy_time, "sell_time": last["time"],
            "buy_price": buy_price, "sell_price": last["close"],
            "reason"   : "장마감",
            "pnl_pct"  : pnl_pct, "net_pnl": _calc_net_pnl(buy_price, last["close"]),
        })

    return results


def _calc_net_pnl(buy: int, sell: int, qty: int = 1) -> float:
    """수수료/세금 차감 후 1주 기준 순손익 (%)"""
    cost_pct = (BUY_FEE + SELL_FEE + SELL_TAX) * 100
    return (sell - buy) / buy * 100 - cost_pct


# ══════════════════════════════════════════
# 다중 종목 / 다중 날짜 백테스트
# ══════════════════════════════════════════

def get_recent_business_days(n: int) -> list[str]:
    """최근 n영업일 날짜 리스트 (오늘 포함, yyyymmdd 형식)"""
    days = []
    d    = date.today()
    while len(days) < n:
        if d.weekday() < 5:   # 주말 제외 (공휴일 미처리)
            days.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return days


def run_multi_backtest(codes: list[tuple[str, str]], days: list[str]) -> None:
    """
    codes: [(code, name), ...]
    days : ["20260314", "20260313", ...]
    """
    all_results = []
    total_calls = len(codes) * len(days)
    call_n      = 0

    print(f"\n  대상 종목: {len(codes)}개 / 기간: {len(days)}일 / 총 조회: {total_calls}회\n")

    for code, name in codes:
        for target_date in days:
            call_n += 1
            date_str = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
            print(f"  [{call_n:>3}/{total_calls}] {name}({code}) {date_str} 분봉 조회...", end="", flush=True)

            candles = fetch_minute_candles(code, target_date, delay=0.15)

            if len(candles) < 30:
                print(f" → 데이터 부족({len(candles)}개) 스킵")
                time.sleep(0.3)
                continue

            trades = backtest_single(code, name, candles)
            print(f" → 분봉 {len(candles)}개 | 매매 {len(trades)}건")

            for t in trades:
                t["code"]        = code
                t["name"]        = name
                t["target_date"] = target_date
                all_results.append(t)

            time.sleep(0.5)   # API 호출 간격

    # ── 결과 통계 출력 ────────────────────────────────────────
    _print_summary(all_results, len(codes), len(days))


def _print_summary(results: list[dict], n_stocks: int, n_days: int) -> None:
    print()
    print("=" * 68)
    print("  📊 REVERSION 백테스트 결과")
    print(f"  기간: {n_days}영업일 / 종목: {n_stocks}개 / 총 매매: {len(results)}건")
    print("=" * 68)

    if not results:
        print("  매매 결과 없음 (스토캐스틱 조건 미충족 또는 데이터 부족)")
        return

    pnls     = [r["net_pnl"]  for r in results]
    wins     = [p for p in pnls if p >= 0]
    losses   = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_pnl  = sum(pnls) / len(pnls) if pnls else 0
    total    = sum(pnls)
    max_win  = max(pnls) if pnls else 0
    max_loss = min(pnls) if pnls else 0

    reasons  = {}
    for r in results:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1

    print(f"  승률     : {len(wins)}/{len(pnls)}  ({win_rate:.1f}%)")
    print(f"  평균수익 : {avg_pnl:+.3f}%")
    print(f"  누적수익 : {total:+.2f}%  (종목 × 날짜 합산)")
    print(f"  최대이익 : {max_win:+.3f}%")
    print(f"  최대손실 : {max_loss:+.3f}%")
    if wins:
        print(f"  평균이익 : {sum(wins)/len(wins):+.3f}%")
    if losses:
        print(f"  평균손실 : {sum(losses)/len(losses):+.3f}%")
        rr = abs(sum(wins)/len(wins)) / abs(sum(losses)/len(losses)) if wins and losses else 0
        print(f"  손익비   : {rr:.2f}")

    print()
    print("  청산 사유 분포:")
    for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
        pct = cnt / len(results) * 100
        print(f"    {reason:20s} {cnt:>4}건  ({pct:4.1f}%)")

    # 날짜별 수익 요약
    if n_days > 1:
        date_pnl: dict[str, list] = {}
        for r in results:
            d = r["target_date"]
            date_pnl.setdefault(d, []).append(r["net_pnl"])
        print()
        print("  날짜별 요약:")
        for d in sorted(date_pnl):
            ps   = date_pnl[d]
            wins_d = sum(1 for p in ps if p >= 0)
            avg_d  = sum(ps) / len(ps)
            date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            icon = "🟢" if avg_d >= 0 else "🔴"
            print(f"    {icon} {date_fmt}  {len(ps):>2}건 │ 승률 {wins_d}/{len(ps)} │ 평균 {avg_d:+.3f}%")

    print("=" * 68)
    print()


# ══════════════════════════════════════════
# 실행 진입점
# ══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="REVERSION 전략 백테스트")
    parser.add_argument("--days", type=int, default=10, help="최근 N 영업일 (기본: 10)")
    parser.add_argument("--top",  type=int, default=30, help="거래량 상위 N종목 스캔 (기본: 30)")
    parser.add_argument("--code", type=str, default="",  help="특정 종목코드 (쉼표 구분, 예: 005930,000660)")
    args = parser.parse_args()

    print()
    print("=" * 68)
    print("  📊 REVERSION 전략 백테스트 (다중 날짜 / 다중 종목)")
    print(f"  전략: 1분봉 스토캐스틱 K<20 골든크로스 매수")
    print(f"  손절 {STOP_LOSS_PCT:+.1f}% / 익절 {TAKE_PROFIT_PCT:+.1f}% / 트레일링 {TRAIL_MIN_PCT:.1f}%→-{TRAIL_DROP_PCT:.1f}%")
    print("=" * 68)

    # ── 토큰 발급 ──────────────────────────────────────────────
    print("\n  KIS 토큰 발급 중...")
    get_access_token()
    print("  완료\n")

    # ── 날짜 리스트 ────────────────────────────────────────────
    days = get_recent_business_days(args.days)
    print(f"  백테스트 기간: {days[-1]} ~ {days[0]}  ({len(days)}영업일)")

    # ── 종목 리스트 ────────────────────────────────────────────
    if args.code:
        raw_codes = [c.strip() for c in args.code.split(",") if c.strip()]
        codes = [(c, c) for c in raw_codes]
        print(f"  지정 종목: {raw_codes}")
    else:
        print(f"  거래량 상위 {args.top}종목 조회 중 (등락률 3~15%)...")
        stocks = fetch_volume_top(top_n=args.top)
        codes  = [(s["code"], s["name"]) for s in stocks]
        print(f"  → {len(codes)}개 종목 선정")
        for c, n in codes:
            print(f"     {n}({c})")

    if not codes:
        print("  종목 없음. 종료.")
        return

    print()
    run_multi_backtest(codes, days)


if __name__ == "__main__":
    main()
