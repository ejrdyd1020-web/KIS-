# ============================================================
#  backtest_compare.py  --  OLD vs NEW REVERSION 전략 비교 백테스트
#
#  사용법:
#    python backtest_compare.py
#    python backtest_compare.py --date 20260311
#    python backtest_compare.py --date 20260311 --codes 005930,000660
#
#  비교 전략:
#    OLD : 1분봉 스토캐스틱 1봉 크로스 + MA120 필터 / 손절 -2.5% / 익절 +3.0%
#    NEW : 1분봉 스토캐스틱 3봉 크로스(K<40 가드) + 5분봉 MA20 + 5분봉 K<50
#          / 손절 -1.5% / 익절 +3.0%
# ============================================================

import sys, os, argparse, time
import requests
import pandas as pd
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8")
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from auth import get_access_token, get_headers, get_base_url
from config import TRADE_COST

BASE_URL = get_base_url()

# 매수+매도 총 비용률 (수수료 0.03% + 세금 0.18% = 0.21%)
_TOTAL_COST_RATE = TRADE_COST["buy_fee"] + TRADE_COST["sell_fee"] + TRADE_COST["sell_tax"]

# ── 전략 파라미터 ────────────────────────────────────────────
OLD = dict(stop_loss=-2.5, take_profit=3.0, cross_window=1, k_guard=None,  use_ma120=True,  use_5min=False)
NEW = dict(stop_loss=-1.5, take_profit=3.0, cross_window=3, k_guard=40.0,  use_ma120=False, use_5min=True)

# ── 스토캐스틱 파라미터 ──────────────────────────────────────
K_PERIOD     = 12
SMOOTH       = 5
D_PERIOD     = 5
MA120_PERIOD = 120
MA20_PERIOD  = 20
OVERSOLD     = 20
OVERBOUGHT   = 80


# ══════════════════════════════════════════════════════════════
# 1. 분봉 데이터 수집
# ══════════════════════════════════════════════════════════════

def fetch_day_candles(code: str, target_date: str) -> list[dict]:
    """
    특정 날짜의 전체 1분봉 수집.
    target_date: "YYYYMMDD"
    반환: [{time, open, high, low, close, volume}, ...] 시간순 정렬

    ※ KIS FHKST03010200 API 한계:
       fid_input_hour_1 은 시간(HHMMSS)만 지원, 날짜 지정 불가.
       → 당일(오늘) 데이터는 16:00 → 09:00 역순으로 정상 수집.
       → 전날 이전은 장 전 시간(< 09:00) 경유를 통해 부분 접근 가능하나
         오후 데이터 재진입이 안 되어 완전한 하루 수집이 어려움.
       따라서 target_date = 오늘 날짜(YYYYMMDD) 사용을 권장.
    """
    today = date.today().strftime("%Y%m%d")
    all_candles = []
    seen        = set()    # (date, time) 중복 방지
    end_time    = "160000"

    for page in range(40):
        try:
            res = requests.get(
                f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                headers=get_headers("FHKST03010200"),
                params={
                    "fid_etc_cls_code"      : "",
                    "fid_cond_mrkt_div_code": "J",
                    "fid_input_iscd"        : code,
                    "fid_input_hour_1"      : end_time,
                    "fid_pw_data_incu_yn"   : "Y",
                },
                timeout=10,
            )
            data = res.json()
        except Exception as e:
            print(f"  [오류] {code} 분봉 조회(page {page}): {e}")
            break

        if data.get("rt_cd") != "0":
            break

        items = data.get("output2", [])
        if not items:
            break

        stop_after = False
        page_found = []

        for c in items:
            row_date = c.get("stck_bsop_date", "")
            row_time = c.get("stck_cntg_hour", "")

            if row_date < target_date:
                stop_after = True
                break
            if row_date != target_date:
                continue

            key = (row_date, row_time)
            if key in seen:
                continue
            seen.add(key)
            page_found.append({
                "time"  : row_time,
                "open"  : int(c.get("stck_oprc", 0) or 0),
                "high"  : int(c.get("stck_hgpr", 0) or 0),
                "low"   : int(c.get("stck_lwpr", 0) or 0),
                "close" : int(c.get("stck_prpr", 0) or 0),
                "volume": int(c.get("cntg_vol", 0) or 0),
            })

        all_candles.extend(page_found)

        if stop_after:
            break

        # 오늘 장 시작까지 수집 완료
        if page_found and page_found[-1]["time"] <= "090100":
            break

        last_time = items[-1].get("stck_cntg_hour", "")
        if not last_time:
            break
        end_time = last_time
        time.sleep(0.15)

    all_candles.sort(key=lambda x: x["time"])
    return all_candles


# ══════════════════════════════════════════════════════════════
# 2. 지표 계산
# ══════════════════════════════════════════════════════════════

from strategy.indicators import calc_stochastic_slow as _calc_stoch_common
from strategy.indicators import build_5min_candles   as _build_5min_common


def calc_stoch(df: pd.DataFrame):
    """공통 모듈 위임 — 백테스트·실매매 동일 로직 보장"""
    return _calc_stoch_common(df, k_period=K_PERIOD,
                              smooth_period=SMOOTH, d_period=D_PERIOD)


def build_5min(df1: pd.DataFrame) -> pd.DataFrame:
    """1분봉 DataFrame → 5분봉 DataFrame (시간순)"""
    candles = df1.to_dict("records")
    rows    = _build_5min_common(candles)
    return pd.DataFrame(rows).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# 3. 전략 시뮬레이션
# ══════════════════════════════════════════════════════════════

def simulate_strategy(code: str, name: str, candles: list[dict], cfg: dict,
                      per_budget: int = 1_666_666, debug: bool = False) -> dict:
    """
    1분봉 candles 위에서 전략 시뮬레이션.
    매수 후 포지션 청산 → 재진입 허용 (일반 단타 방식).

    per_budget: 종목당 투자금액 (원). qty = per_budget // buy_price

    Returns:
        {
          "code", "name",
          "trades": [{entry_time, exit_time, buy_price, sell_price,
                      qty, invested, profit_won, pct, reason}, ...]
        }
    """
    if len(candles) < 40:
        return {"code": code, "name": name, "trades": []}

    df = pd.DataFrame(candles)   # 시간순 (과거→최신)

    # 1분봉 지표
    sk1, sd1  = calc_stoch(df)
    ma120_s   = df["close"].rolling(MA120_PERIOD).mean()

    # 5분봉 지표
    df5        = build_5min(df)
    sk5, sd5   = (pd.Series(dtype=float), pd.Series(dtype=float))
    ma20_5     = pd.Series(dtype=float)
    if len(df5) >= 22:
        sk5, sd5 = calc_stoch(df5)
        ma20_5   = df5["close"].rolling(MA20_PERIOD).mean()

    trades        = []
    position      = None
    in_position   = False

    # 디버그용 필터 차단 카운터
    dbg = dict(k_guard=0, no_cross=0, ma120=0,
               j5_early=0, ma20_below=0, k5_over=0, passed=0)

    for i in range(max(K_PERIOD + SMOOTH + D_PERIOD, MA120_PERIOD) + 1, len(df)):
        row   = df.iloc[i]
        t     = row["time"]
        close = row["close"]

        if t < "0910" or t > "1520":
            continue

        # ── 포지션 청산 체크 ──────────────────────────────────
        if in_position:
            def _make_trade(sell_price, exit_time, reason):
                qty        = position["qty"]
                buy_p      = position["buy"]
                fee_won    = int((buy_p * qty * TRADE_COST["buy_fee"])
                               + (sell_price * qty * TRADE_COST["sell_fee"])
                               + (sell_price * qty * TRADE_COST["sell_tax"]))
                gross_won  = (sell_price - buy_p) * qty
                profit_won = gross_won - fee_won
                pct        = (sell_price - buy_p) / buy_p * 100
                net_pct    = profit_won / (buy_p * qty) * 100
                return {
                    "entry_time" : position["entry_time"],
                    "exit_time"  : exit_time,
                    "buy_price"  : buy_p,
                    "sell_price" : sell_price,
                    "qty"        : qty,
                    "invested"   : buy_p * qty,
                    "fee_won"    : fee_won,
                    "profit_won" : profit_won,   # 비용 차감 순손익
                    "pct"        : pct,          # 순수 가격 변동률
                    "net_pct"    : net_pct,      # 비용 포함 실질 수익률
                    "reason"     : reason,
                }
            if close <= position["stop"]:
                trades.append(_make_trade(close, t, "손절"))
                in_position = False
                continue
            elif close >= position["take"]:
                trades.append(_make_trade(close, t, "익절"))
                in_position = False
                continue
            elif t >= "1520":
                trades.append(_make_trade(close, t, "장마감"))
                in_position = False
            continue

        # ── 진입 조건 체크 ────────────────────────────────────
        k_cur  = sk1.iloc[i]
        d_cur  = sd1.iloc[i]
        if pd.isna(k_cur) or pd.isna(d_cur):
            continue

        # ▶ 공통: K값 가드 (NEW 전략)
        if cfg["k_guard"] and k_cur >= cfg["k_guard"]:
            dbg["k_guard"] += 1
            continue

        # ▶ 골든크로스 감지 (cross_window봉 이내)
        golden = False
        for w in range(1, cfg["cross_window"] + 1):
            idx_cur  = i
            idx_prev = i - w
            if idx_prev < 0:
                break
            ck = sk1.iloc[idx_cur]; cd = sd1.iloc[idx_cur]
            pk = sk1.iloc[idx_prev]; pd_ = sd1.iloc[idx_prev]
            if any(pd.isna(v) for v in [ck, cd, pk, pd_]):
                continue
            if ck > cd and pk <= pd_ and min(pk, pd_) <= OVERSOLD:
                golden = True
                break

        if not golden:
            dbg["no_cross"] += 1
            continue

        # ▶ MA120 필터 (OLD 전략)
        if cfg["use_ma120"]:
            ma120_val = ma120_s.iloc[i]
            if pd.isna(ma120_val) or close < ma120_val:
                dbg["ma120"] += 1
                continue

        # ▶ 5분봉 필터 (NEW 전략)
        # ※ j5-1 : 현재 형성 중인 5분봉 제외, 완성된 직전봉 기준
        if cfg["use_5min"] and len(df5) >= 22:
            j5 = len([x for x in df5["time"] if x <= t]) - 1 - 1  # -1: 미완성봉 제외
            if j5 < 20:
                dbg["j5_early"] += 1
                continue
            ma20_val = ma20_5.iloc[j5]
            k5_val   = sk5.iloc[j5]
            if pd.isna(ma20_val) or pd.isna(k5_val):
                dbg["j5_early"] += 1
                continue
            if close < ma20_val:
                dbg["ma20_below"] += 1
                if debug:
                    print(f"      [{t[:2]}:{t[2:4]}] 5분봉MA20차단: "
                          f"현재가{close:,} < MA20{ma20_val:,.0f}")
                continue
            if k5_val >= 50:
                dbg["k5_over"] += 1
                if debug:
                    print(f"      [{t[:2]}:{t[2:4]}] 5분봉K과열차단: "
                          f"K5={k5_val:.1f}>=50")
                continue

        dbg["passed"] += 1

        # ── 진입 ─────────────────────────────────────────────
        buy_price = close
        qty       = max(1, per_budget // buy_price)
        stop      = int(buy_price * (1 + cfg["stop_loss"] / 100))
        take      = int(buy_price * (1 + cfg["take_profit"] / 100))
        in_position = True
        position    = {
            "buy"       : buy_price,
            "qty"       : qty,
            "stop"      : stop,
            "take"      : take,
            "entry_time": t,
        }

    # 장마감 미청산 처리
    if in_position and len(candles) > 0:
        trades.append(_make_trade(candles[-1]["close"], candles[-1]["time"], "장마감"))

    return {"code": code, "name": name, "trades": trades, "dbg": dbg}


# ══════════════════════════════════════════════════════════════
# 4. 결과 출력
# ══════════════════════════════════════════════════════════════

def print_result(label: str, results: list[dict], cfg: dict):
    all_trades = []
    for r in results:
        for t in r["trades"]:
            t["name"] = r["name"]
            t["code"] = r["code"]
            all_trades.append(t)

    wins        = [t for t in all_trades if t["profit_won"] >= 0]
    losses      = [t for t in all_trades if t["profit_won"] < 0]
    total       = len(all_trades)
    total_won   = sum(t["profit_won"] for t in all_trades)
    total_inv   = sum(t["invested"]   for t in all_trades)
    avg_pct     = sum(t["pct"]        for t in all_trades) / total if total else 0.0
    win_won     = sum(t["profit_won"] for t in wins)
    loss_won    = abs(sum(t["profit_won"] for t in losses))
    pf          = win_won / loss_won if loss_won > 0 else float("inf")

    print(f"\n{'='*82}")
    print(f"  [{label}]  손절 {cfg['stop_loss']:+.1f}% / 익절 +{cfg['take_profit']:.1f}%  "
          f"(종목당 {all_trades[0]['invested']:,}원 기준)" if all_trades else
          f"\n{'='*82}\n  [{label}]  손절 {cfg['stop_loss']:+.1f}% / 익절 +{cfg['take_profit']:.1f}%")
    print(f"{'='*82}")
    print(f"  {'':2} {'종목':10} {'시간':14} {'매수가':>8} {'매도가':>8} "
          f"{'수량':>5} {'투자금':>11} {'비용':>7} {'순손익(원)':>12} {'실질%':>7}  사유")
    print(f"  {'-'*88}")
    for t in all_trades:
        sign  = "+" if t["profit_won"] >= 0 else ""
        icon  = "O" if t["profit_won"] >= 0 else "X"
        won_s = f"{sign}{t['profit_won']:,}"
        pct_s = f"{sign}{t['net_pct']:.2f}%"
        print(f"  {icon} {t['name']:10} "
              f"{t['entry_time'][:2]}:{t['entry_time'][2:4]}"
              f"→{t['exit_time'][:2]}:{t['exit_time'][2:4]}  "
              f"{t['buy_price']:>8,}  {t['sell_price']:>8,}  "
              f"{t['qty']:>5}주  "
              f"{t['invested']:>10,}원  "
              f"{t['fee_won']:>6,}원  "
              f"{won_s:>11}원  "
              f"{pct_s:>7}  {t['reason']}")
    print(f"  {'-'*80}")
    if total == 0:
        print(f"  총 거래: 0회  (해당 날짜 진입 신호 없음)")
    else:
        roi = total_won / total_inv * 100 if total_inv > 0 else 0
        sign = "+" if total_won >= 0 else ""
        print(f"  총 {total}회  |  익절 {len(wins)}회 / 손절 {len(losses)}회  "
              f"|  승률 {len(wins)/total*100:.0f}%  |  평균 {avg_pct:+.2f}%  |  PF {pf:.2f}")
        print(f"  총투자: {total_inv:,}원  |  총손익: {sign}{total_won:,}원  |  ROI: {sign}{roi:.2f}%")


def print_comparison(old_results, new_results):
    """두 전략 핵심 지표 비교 요약 (% + 원 단위 병기)"""
    def stats(results):
        trades = [t for r in results for t in r["trades"]]
        if not trades:
            return dict(n=0, wins=0, losses=0, win_rate=0,
                        avg_pct=0, total_pct=0, total_won=0,
                        total_inv=0, roi=0, pf=0)
        wins      = [t for t in trades if t["profit_won"] >= 0]
        losses    = [t for t in trades if t["profit_won"] < 0]
        total_won = sum(t["profit_won"] for t in trades)
        total_inv = sum(t["invested"]   for t in trades)
        total_pct = sum(t["pct"]        for t in trades)
        avg_pct   = total_pct / len(trades)
        win_won   = sum(t["profit_won"] for t in wins)
        loss_won  = abs(sum(t["profit_won"] for t in losses))
        pf        = win_won / loss_won if loss_won > 0 else float("inf")
        roi       = total_won / total_inv * 100 if total_inv > 0 else 0
        return dict(n=len(trades), wins=len(wins), losses=len(losses),
                    win_rate=len(wins)/len(trades)*100,
                    avg_pct=avg_pct, total_pct=total_pct,
                    total_won=total_won, total_inv=total_inv,
                    roi=roi, pf=pf)

    o = stats(old_results)
    n = stats(new_results)

    def fmt_won(v):
        return f"{'+' if v >= 0 else ''}{v:,}원"

    print(f"\n{'='*62}")
    print(f"  [OLD vs NEW 전략 비교]")
    print(f"{'='*62}")
    print(f"  {'항목':18}  {'OLD':>18}  {'NEW':>18}")
    print(f"  {'-'*58}")
    print(f"  {'거래 횟수':18}  {o['n']:>17}회  {n['n']:>17}회")
    print(f"  {'익절/손절':18}  {o['wins']}승 {o['losses']}패{'':<12}  {n['wins']}승 {n['losses']}패")
    print(f"  {'승률':18}  {o['win_rate']:>17.1f}%  {n['win_rate']:>17.1f}%")
    print(f"  {'평균 수익률':18}  {o['avg_pct']:>+17.2f}%  {n['avg_pct']:>+17.2f}%")
    print(f"  {'누적 수익률':18}  {o['total_pct']:>+17.2f}%  {n['total_pct']:>+17.2f}%")
    print(f"  {'총 투자금':18}  {fmt_won(o['total_inv']):>19}  {fmt_won(n['total_inv']):>19}")
    print(f"  {'총 손익(원)':18}  {fmt_won(o['total_won']):>19}  {fmt_won(n['total_won']):>19}")
    print(f"  {'ROI':18}  {o['roi']:>+17.2f}%  {n['roi']:>+17.2f}%")
    print(f"  {'Profit Factor':18}  {o['pf']:>18.2f}  {n['pf']:>18.2f}")
    print(f"{'='*62}\n")


# ══════════════════════════════════════════════════════════════
# 5. 메인
# ══════════════════════════════════════════════════════════════

DEFAULT_CODES = [
    # 최근 REVERSION 스캔 대상 종목 (필요 시 추가/수정)
    ("032820", "이엔에프"),
    ("279570", "EHR코리아"),
    ("000660", "SK하이닉스"),
    ("005930", "삼성전자"),
    ("035720", "카카오"),
    ("035420", "NAVER"),
    ("086520", "에코프로"),
    ("373220", "LG에너지솔루션"),
]


def today_str() -> str:
    """오늘 날짜 YYYYMMDD"""
    return date.today().strftime("%Y%m%d")


def main():
    parser = argparse.ArgumentParser(description="OLD vs NEW REVERSION 백테스트")
    parser.add_argument("--date",  default="",   help="대상 날짜 YYYYMMDD (기본: 오늘)")
    parser.add_argument("--codes", default="",   help="종목코드 콤마 구분 (기본: DEFAULT_CODES)")
    parser.add_argument("--rank",  action="store_true",
                        help="오늘 거래량 순위 상위 종목 자동 조회 (등락률 3~20%%)")
    parser.add_argument("--top",    type=int, default=20,
                        help="--rank 사용 시 상위 N개 종목 (기본: 20)")
    parser.add_argument("--budget", type=int, default=1_666_666,
                        help="종목당 투자금액 원 (기본: 1,666,666)")
    parser.add_argument("--debug",  action="store_true",
                        help="필터별 차단 현황 상세 출력")
    args = parser.parse_args()

    target_date = args.date if args.date else today_str()
    codes_raw   = args.codes

    get_access_token()

    if args.rank:
        print("  거래량 순위 조회 중 (등락률 3~20%)...")
        try:
            from api.price import get_volume_rank
            rank_stocks = get_volume_rank(top_n=args.top, min_change_rate=3.0, max_change_rate=20.0)
            code_list = [(s["code"], s["name"]) for s in rank_stocks]
            print(f"  → {len(code_list)}개 종목 조회 완료")
        except Exception as e:
            print(f"  거래량 순위 조회 실패: {e} → DEFAULT_CODES 사용")
            code_list = DEFAULT_CODES
    elif codes_raw:
        code_list = [(c.strip(), c.strip()) for c in codes_raw.split(",")]
    else:
        code_list = DEFAULT_CODES

    print(f"""
======================================================
  OLD vs NEW REVERSION 전략 백테스트
  대상일  : {target_date[:4]}.{target_date[4:6]}.{target_date[6:]}
  종목 수 : {len(code_list)}개
------------------------------------------------------
  [OLD] 1봉 크로스 + MA120 / SL -2.5% / TP +3.0%
  [NEW] 3봉 크로스(K<40) + 5분봉MA20 + 5분봉K<50
        / SL -1.5% / TP +3.0%
  종목당 투자금 : {args.budget:,}원
======================================================""")

    old_results = []
    new_results = []

    for code, name in code_list:
        print(f"\n  [{name}({code})] 분봉 수집 중...")
        candles = fetch_day_candles(code, target_date)
        print(f"  → {len(candles)}봉 수집")

        if len(candles) < 40:
            print(f"  → 데이터 부족 - 스킵")
            continue

        if args.debug:
            print(f"  [NEW 필터 진단]")
        r_old = simulate_strategy(code, name, candles, OLD, per_budget=args.budget)
        r_new = simulate_strategy(code, name, candles, NEW, per_budget=args.budget,
                                  debug=args.debug)
        old_results.append(r_old)
        new_results.append(r_new)

        o_cnt = len(r_old["trades"])
        n_cnt = len(r_new["trades"])
        print(f"  → OLD {o_cnt}회 / NEW {n_cnt}회 거래 발생")

        if args.debug:
            d = r_new["dbg"]
            total_chk = sum(d.values())
            print(f"     ┌─ K가드(K≥40)  차단: {d['k_guard']:>4}회")
            print(f"     ├─ 크로스없음    차단: {d['no_cross']:>4}회")
            print(f"     ├─ MA120(OLD용) 차단: {d['ma120']:>4}회")
            print(f"     ├─ 5분봉데이터부족차단: {d['j5_early']:>4}회")
            print(f"     ├─ 5분봉MA20↓  차단: {d['ma20_below']:>4}회")
            print(f"     ├─ 5분봉K≥50   차단: {d['k5_over']:>4}회")
            print(f"     └─ 진입 통과         : {d['passed']:>4}회")
        time.sleep(0.3)

    if not old_results and not new_results:
        print("\n데이터 없음 - 종료")
        return

    print_result("OLD 전략", old_results, OLD)
    print_result("NEW 전략", new_results, NEW)
    print_comparison(old_results, new_results)


if __name__ == "__main__":
    main()
