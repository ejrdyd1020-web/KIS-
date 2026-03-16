# ============================================================
#  status.py  –  자동매매 실시간 거래현황 대시보드
#
#  실행 방법:
#    python status.py          # 1회 출력
#    python status.py --watch  # 10초마다 자동 갱신
# ============================================================

import json
import os
import re
import sys
import time
from datetime import datetime, date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ── 경로 설정 ──────────────────────────────────────────────
sys.path.append(str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

POSITIONS_FILE = BASE_DIR / "data" / "positions.json"
LOG_DIR        = BASE_DIR / "logs"


# ══════════════════════════════════════════
# 1. 포지션 파일 읽기 (autotrader 프로세스 외부 실행)
# ══════════════════════════════════════════

def load_positions() -> dict:
    if not POSITIONS_FILE.exists():
        return {}
    try:
        return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ══════════════════════════════════════════
# 2. 당일 로그 파싱 → 매매 내역 추출
# ══════════════════════════════════════════

def parse_today_trades() -> list[dict]:
    """
    autotrader.log 에서 오늘 날짜의 매수/매도/실현손익 라인 파싱.
    반환: [{"time", "type", "name", "code", "qty", "price", "pnl", "reason"}, ...]
    """
    today_str = date.today().strftime("%Y-%m-%d")
    trades    = []

    # 최신 로그 파일 찾기 (TimedRotatingFileHandler 사용 시 날짜별 파일 생성)
    log_files = sorted(LOG_DIR.glob("autotrader*.log"), reverse=True)
    if not log_files:
        return []

    buy_pattern  = re.compile(
        r"(\d{2}:\d{2}:\d{2}).*\[(.+?)\((\w+)\)\].*✅ 매수 성공.*?(\d+)주.*?시장가"
    )
    sell_pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}).*\[(.+?)\] ✅ (.+?) 완료"
    )
    pnl_pattern  = re.compile(
        r"(\d{2}:\d{2}:\d{2}).*\[(.+?)\((\w+)\)\] 실현손익.*순손익: ([+-][\d,]+)원 \(([+-][\d.]+)%\)"
    )
    buy_pattern2 = re.compile(
        r"(\d{2}:\d{2}:\d{2}).*\[(\w+)\] ✅ 매수 성공 \| (\d+)주 시장가 \| 주문번호"
    )
    # 포지션 등록 라인 (이름+코드 포함)
    pos_pattern  = re.compile(
        r"(\d{2}:\d{2}:\d{2}).*\[(.+?)\((\w+)\)\] 포지션 등록.*매입가: ([\d,]+)원.*손절가: ([\d,]+)원"
    )

    # 실현손익 라인 파싱 (가장 정확)
    pnl_records: dict[str, dict] = {}

    for log_file in log_files[:2]:  # 최신 2개 파일만
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        for line in lines:
            if today_str not in line:
                continue

            # 실현손익 파싱
            m = pnl_pattern.search(line)
            if m:
                t, name, code, pnl_str, pnl_pct = m.groups()
                pnl = int(pnl_str.replace(",", ""))
                pnl_records[code] = {
                    "time"   : t,
                    "name"   : name,
                    "code"   : code,
                    "pnl"    : pnl,
                    "pnl_pct": float(pnl_pct),
                }

            # 포지션 등록 (= 매수 완료)
            m2 = pos_pattern.search(line)
            if m2:
                t, name, code, price_str, stop_str = m2.groups()
                price = int(price_str.replace(",", ""))
                existing = next((x for x in trades if x["code"] == code and x["type"] == "BUY"), None)
                if not existing:
                    trades.append({
                        "time"   : t,
                        "type"   : "BUY",
                        "name"   : name,
                        "code"   : code,
                        "price"  : price,
                        "pnl"    : None,
                        "pnl_pct": None,
                        "reason" : "",
                    })

    # 매도(실현손익) 레코드 추가
    for code, rec in pnl_records.items():
        trades.append({
            "time"   : rec["time"],
            "type"   : "SELL",
            "name"   : rec["name"],
            "code"   : code,
            "price"  : None,
            "pnl"    : rec["pnl"],
            "pnl_pct": rec["pnl_pct"],
            "reason" : "",
        })

    trades.sort(key=lambda x: x["time"])
    return trades


# ══════════════════════════════════════════
# 3. KIS API – 잔고 + 현재가
# ══════════════════════════════════════════

def fetch_balance_and_prices(positions: dict) -> tuple[dict, dict]:
    """
    KIS 잔고 API로 예수금/총평가액 조회 + 보유 종목 현재가 일괄 조회.

    Returns:
        (balance_info, price_map)
        balance_info: {"deposit", "total_eval", "total_profit"}
        price_map:    {code: {"price", "change_rate"}}
    """
    try:
        from auth         import get_access_token
        from api.balance  import get_balance
        from api.price    import get_current_price

        get_access_token()

        bal_data = get_balance()
        balance_info = {
            "deposit"     : bal_data.get("deposit", 0),
            "total_eval"  : bal_data.get("total_eval", 0),
            "total_profit": bal_data.get("total_profit", 0),
        }

        price_map = {}
        for code in positions:
            info = get_current_price(code)
            if info:
                price_map[code] = {
                    "price"      : info.get("price", 0),
                    "change_rate": info.get("change_rate", 0.0),
                }
        return balance_info, price_map

    except Exception as e:
        print(f"  [경고] KIS API 조회 실패: {e}")
        return {"deposit": 0, "total_eval": 0, "total_profit": 0}, {}


# ══════════════════════════════════════════
# 4. 출력
# ══════════════════════════════════════════

def print_status():
    now_str   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_str = date.today().strftime("%Y/%m/%d")

    print()
    print("=" * 68)
    print(f"  📊 KIS 자동매매 거래현황   [{now_str}]")
    print("=" * 68)

    # ── 포지션 로드 ───────────────────────────────────────────
    positions = load_positions()

    # ── KIS API 조회 ──────────────────────────────────────────
    print("  ⏳ KIS API 조회 중...")
    balance_info, price_map = fetch_balance_and_prices(positions)

    # ── 1. 계좌 요약 ───────────────────────────────────────────
    deposit      = balance_info["deposit"]
    total_eval   = balance_info["total_eval"]
    total_profit = balance_info["total_profit"]

    print()
    print("  ┌─ 계좌 요약 ─────────────────────────────────────┐")
    print(f"  │  매수가능금액 : {deposit:>14,} 원               │")
    print(f"  │  주식 평가금액: {total_eval:>14,} 원               │")
    sign = "+" if total_profit >= 0 else ""
    print(f"  │  평가손익     : {sign}{total_profit:>13,} 원               │")
    print("  └─────────────────────────────────────────────────┘")

    # ── 2. 현재 보유 포지션 ────────────────────────────────────
    print()
    print(f"  📋 보유 포지션 ({len(positions)}개)")
    print("  " + "-" * 66)

    if not positions:
        print("  보유 포지션 없음")
    else:
        total_book  = 0
        total_eval2 = 0
        for code, pos in positions.items():
            avg   = pos.get("avg_price", 0)
            qty   = pos.get("qty", 0)
            name  = pos.get("name", code)
            strat = pos.get("strategy_type", "?")
            stop  = pos.get("hard_stop", 0)
            tp    = pos.get("take_profit", 0)
            mxp   = pos.get("max_price", avg)
            book  = avg * qty
            total_book += book

            cur  = price_map.get(code, {}).get("price", 0)
            cr   = price_map.get(code, {}).get("change_rate", 0.0)
            if cur:
                pnl_pct = (cur - avg) / avg * 100
                pnl_amt = (cur - avg) * qty
                eval_   = cur * qty
                total_eval2 += eval_
            else:
                pnl_pct = 0.0
                pnl_amt = 0
                eval_   = book
                total_eval2 += eval_

            icon = "🟢" if pnl_pct >= 0 else "🔴"
            sign2 = "+" if pnl_pct >= 0 else ""
            pnl_sign = "+" if pnl_amt >= 0 else ""

            bought_at = pos.get("bought_at", "")
            if bought_at:
                try:
                    dt = datetime.fromisoformat(bought_at)
                    bought_str = dt.strftime("%H:%M")
                except Exception:
                    bought_str = bought_at[:5]
            else:
                bought_str = "-"

            print(f"  {icon} {name}({code})  [{strat}]  매수:{bought_str}")
            print(f"     매입가: {avg:,}원 × {qty:,}주 = {book:,}원")
            if cur:
                print(f"     현재가: {cur:,}원 ({cr:+.2f}%)  │  "
                      f"수익: {sign2}{pnl_pct:.2f}% ({pnl_sign}{pnl_amt:,}원)")
            print(f"     손절가: {stop:,}원  │  익절가: {tp:,}원  │  고점: {mxp:,}원")
            print()

        print(f"  매수금액 합계: {total_book:,}원  →  평가금액: {total_eval2:,}원")

    # ── 3. 당일 매매 내역 ──────────────────────────────────────
    print()
    print(f"  📅 {today_str} 매매 내역")
    print("  " + "-" * 66)

    trades = parse_today_trades()
    if not trades:
        print("  오늘 매매 내역 없음 (로그 미확인 또는 없음)")
    else:
        buy_cnt  = sum(1 for t in trades if t["type"] == "BUY")
        sell_cnt = sum(1 for t in trades if t["type"] == "SELL")
        win_cnt  = sum(1 for t in trades if t["type"] == "SELL" and t["pnl"] and t["pnl"] >= 0)
        total_pnl = sum(t["pnl"] for t in trades if t["type"] == "SELL" and t["pnl"] is not None)

        print(f"  매수 {buy_cnt}건  │  매도 {sell_cnt}건  │  "
              f"승률 {win_cnt}/{sell_cnt}  │  "
              f"당일 실현손익: {total_pnl:+,}원")
        print()

        for t in trades:
            icon = "🟢" if t["type"] == "BUY" else ("💰" if (t["pnl"] or 0) >= 0 else "🔴")
            type_str = "매수" if t["type"] == "BUY" else "매도"

            if t["type"] == "BUY":
                price_str = f"{t['price']:,}원" if t["price"] else "-"
                print(f"  {t['time']}  {icon} {type_str}  {t['name']}({t['code']})  {price_str}")
            else:
                pnl_str = f"{t['pnl']:+,}원 ({t['pnl_pct']:+.2f}%)" if t["pnl"] is not None else "-"
                print(f"  {t['time']}  {icon} {type_str}  {t['name']}({t['code']})  {pnl_str}")

    print()
    print("=" * 68)
    print()


# ══════════════════════════════════════════
# 5. 실행
# ══════════════════════════════════════════

if __name__ == "__main__":
    watch_mode = "--watch" in sys.argv

    if watch_mode:
        interval = 10
        print(f"  [watch 모드] {interval}초마다 자동 갱신 (종료: Ctrl+C)")
        try:
            while True:
                os.system("cls" if os.name == "nt" else "clear")
                print_status()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n  종료")
    else:
        print_status()
