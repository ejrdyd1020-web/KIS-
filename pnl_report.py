# ============================================================
#  pnl_report.py  –  일별·당일 손익 리포트 (스크롤 TUI)
#
#  실행: python pnl_report.py
#  키:   ↑↓ / PgUp PgDn 스크롤 | Tab 탭전환 | q 종료
# ============================================================

import re, sys, os, msvcrt
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR  = BASE_DIR / "logs"

BUY_FEE_RATE  = 0.00015
SELL_FEE_RATE = 0.00015
SELL_TAX_RATE = 0.0018

# ── ANSI 색상 ──────────────────────────────────────────────
R   = '\033[91m'   # 빨강 (수익)
B   = '\033[94m'   # 파랑 (손실)
Y   = '\033[93m'   # 노랑 (헤더)
W   = '\033[97m'   # 흰색
GR  = '\033[90m'   # 회색 (구분선)
RST = '\033[0m'
BLD = '\033[1m'
REV = '\033[7m'    # 반전 (선택/상태바)
HID = '\033[?25l'  # 커서 숨김
SHW = '\033[?25h'  # 커서 표시

def clr_scr():
    print('\033[2J\033[H', end='', flush=True)

def move(row, col=0):
    print(f'\033[{row+1};{col+1}H', end='', flush=True)

def term_size():
    try:
        s = os.get_terminal_size()
        return s.lines, s.columns
    except Exception:
        return 40, 120

# ── 로그 파싱 ──────────────────────────────────────────────
PNL_RE = re.compile(
    r"\[(\d{2}:\d{2}:\d{2})\].*\[(.+?)\((\w+)\)\] 실현손익 기록 \| "
    r"총손익: ([+-][\d,]+)원 → 비용\(([\d,]+)원\) 차감 → "
    r"순손익: ([+-][\d,]+)원 \(([+-][\d.]+)%\)"
)
POS_RE = re.compile(
    r"\[(\d{2}:\d{2}:\d{2})\].*\[(.+?)\((\w+)\)\] 포지션 등록 \| 전략: (\w+) \| 매입가: ([\d,]+)원"
)
BUY_TRY_RE = re.compile(
    r"\[(\d{2}:\d{2}:\d{2})\].*\[(.+?)\((\w+)\)\].*매수 시도 \| (\d+)주 × ([\d,]+)원"
)

def parse_log_file(log_path, date_str):
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    buy_map   = {}
    for m in BUY_TRY_RE.finditer(text):
        _, _, code, qty_s, price_s = m.groups()
        buy_map[code] = (int(qty_s), int(price_s.replace(",", "")))

    strat_map = {}
    pos_price = {}
    for m in POS_RE.finditer(text):
        _, _, code, strategy, price_s = m.groups()
        strat_map[code] = strategy
        pos_price[code] = int(price_s.replace(",", ""))

    records = []
    for m in PNL_RE.finditer(text):
        t, name, code, gross_s, fee_s, net_s, pct_s = m.groups()
        gross = int(gross_s.replace(",", ""))
        net   = int(net_s.replace(",", ""))
        pct   = float(pct_s)

        if code in buy_map:
            qty, avg_price = buy_map[code]
            buy_amt = qty * avg_price
        elif code in pos_price:
            avg_price = pos_price[code]
            buy_amt   = int(abs(gross / (pct / 100))) if pct != 0 else 0
            qty = buy_amt // avg_price if avg_price else 0
        else:
            qty, avg_price, buy_amt = 0, 0, 0

        sell_amt  = buy_amt + gross
        sell_price = sell_amt // qty if qty else 0
        fee_amt   = int(buy_amt * BUY_FEE_RATE + sell_amt * SELL_FEE_RATE)
        tax_amt   = int(sell_amt * SELL_TAX_RATE)

        records.append({
            "date": date_str, "time": t,
            "name": name, "code": code,
            "strategy": strat_map.get(code, "?"),
            "qty": qty, "buy_price": avg_price, "sell_price": sell_price,
            "buy_amt": buy_amt, "sell_amt": sell_amt,
            "gross": gross, "fee": fee_amt, "tax": tax_amt,
            "net": net, "pct": pct,
        })
    return records


def collect_all_records():
    all_records = []
    for log_file in sorted(LOG_DIR.glob("2???-??-??.log")):
        all_records.extend(parse_log_file(log_file, log_file.stem))

    today_str  = date.today().isoformat()
    today_log  = LOG_DIR / "autotrader.log"
    dated_file = LOG_DIR / f"{today_str}.log"
    if today_log.exists() and not dated_file.exists():
        all_records.extend(parse_log_file(today_log, today_str))

    return sorted(all_records, key=lambda x: (x["date"], x["time"]))


def dow(date_str):
    try:
        return ["월","화","수","목","금","토","일"][
            datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except Exception:
        return ""

def cw(s):
    """한글 포함 문자열의 표시 너비 계산"""
    w = 0
    for c in s:
        w += 2 if '\uac00' <= c <= '\ud7a3' or '\u3000' <= c <= '\u9fff' else 1
    return w

def pad(s, width, align='right'):
    """표시 너비 기준으로 패딩 적용"""
    w = cw(s)
    diff = width - w
    diff = max(0, diff)
    if align == 'right':
        return ' ' * diff + s
    elif align == 'left':
        return s + ' ' * diff
    else:  # center
        left = diff // 2
        return ' ' * left + s + ' ' * (diff - left)

def colored(n, s):
    """수익 빨강, 손실 파랑"""
    return (R if n >= 0 else B) + s + RST

def signed(n):
    return f"+{n:,}" if n >= 0 else f"{n:,}"

def pct_s(p):
    return f"{p:+.2f}%"


# ══════════════════════════════════════════
# 일별 실현손익 테이블 행 생성
# ══════════════════════════════════════════

# 컬럼 정의: (헤더, 너비, 정렬)
DAILY_COLS = [
    ("매매일",    14, 'left'),
    ("매수금액",   13, 'right'),
    ("매도금액",   13, 'right'),
    ("실현손익",   12, 'right'),
    ("수익률",     8, 'right'),
    ("수수료",     9, 'right'),
    ("세금",       9, 'right'),
]

TODAY_COLS = [
    ("시간",       8, 'left'),
    ("종목명",    14, 'left'),
    ("코드",       8, 'center'),
    ("전략",      10, 'center'),
    ("수량",       6, 'right'),
    ("매수단가",   10, 'right'),
    ("매도단가",   10, 'right'),
    ("매수금액",   13, 'right'),
    ("매도금액",   13, 'right'),
    ("실현손익",   12, 'right'),
    ("수익률",     8, 'right'),
    ("수수료",     8, 'right'),
    ("세금",       8, 'right'),
]


def make_sep(cols, left='├', mid='┼', right='┤', fill='─'):
    parts = [fill * (c[1] + 2) for c in cols]
    return left + mid.join(parts) + right

def make_top(cols):
    parts = ['─' * (c[1] + 2) for c in cols]
    return '┌' + '┬'.join(parts) + '┐'

def make_bot(cols):
    parts = ['─' * (c[1] + 2) for c in cols]
    return '└' + '┴'.join(parts) + '┘'

def make_header_row(cols):
    cells = [' ' + pad(c[0], c[1], 'center') + ' ' for c in cols]
    return GR + '│' + RST + (GR + '│' + RST).join(
        Y + BLD + cell + RST for cell in cells
    ) + GR + '│' + RST

def make_data_row(cols, values, net=None):
    """values: list of (text, color_or_None)"""
    cells = []
    for (_, width, align), (text, color) in zip(cols, values):
        padded = ' ' + pad(text, width, align) + ' '
        if color:
            cells.append(color + padded + RST)
        else:
            cells.append(padded)
    return GR + '│' + RST + (GR + '│' + RST).join(cells) + GR + '│' + RST


def build_daily_rows(by_date):
    rows = []
    for d in sorted(by_date.keys(), reverse=True):  # 최신 날짜 위
        recs     = by_date[d]
        buy_tot  = sum(r["buy_amt"]  for r in recs)
        sell_tot = sum(r["sell_amt"] for r in recs)
        net_tot  = sum(r["net"]      for r in recs)
        fee_tot  = sum(r["fee"]      for r in recs)
        tax_tot  = sum(r["tax"]      for r in recs)
        gross_t  = sum(r["gross"]    for r in recs)
        wr       = gross_t / buy_tot * 100 if buy_tot else 0

        nc = R if net_tot >= 0 else B
        pc = R if wr      >= 0 else B

        values = [
            (f"{d}({dow(d)})", None),
            (f"{buy_tot:,}",   None),
            (f"{sell_tot:,}",  None),
            (signed(net_tot),  nc),
            (pct_s(wr),        pc),
            (f"{fee_tot:,}",   None),
            (f"{tax_tot:,}",   None),
        ]
        rows.append(make_data_row(DAILY_COLS, values))
    return rows


def build_daily_total(by_date):
    all_recs = [r for recs in by_date.values() for r in recs]
    buy_tot  = sum(r["buy_amt"]  for r in all_recs)
    sell_tot = sum(r["sell_amt"] for r in all_recs)
    net_tot  = sum(r["net"]      for r in all_recs)
    fee_tot  = sum(r["fee"]      for r in all_recs)
    tax_tot  = sum(r["tax"]      for r in all_recs)
    gross_t  = sum(r["gross"]    for r in all_recs)
    win      = sum(1 for r in all_recs if r["net"] >= 0)
    cnt      = len(all_recs)
    wr       = gross_t / buy_tot * 100 if buy_tot else 0
    nc = R if net_tot >= 0 else B
    pc = R if wr >= 0 else B

    return (
        make_data_row(DAILY_COLS, [
            (f"합계 {win}승{cnt-win}패", BLD),
            (f"{buy_tot:,}",   None),
            (f"{sell_tot:,}",  None),
            (signed(net_tot),  nc),
            (pct_s(wr),        pc),
            (f"{fee_tot:,}",   None),
            (f"{tax_tot:,}",   None),
        ]),
        {"buy": buy_tot, "sell": sell_tot, "net": net_tot,
         "fee": fee_tot, "tax": tax_tot, "gross": gross_t,
         "win": win, "cnt": cnt, "wr": wr}
    )


def build_today_rows(today_recs):
    rows = []
    for r in today_recs:
        nc = R if r["net"] >= 0 else B
        pc = R if r["pct"] >= 0 else B
        values = [
            (r["time"],                  None),
            (r["name"][:14],             None),
            (r["code"],                  None),
            (r["strategy"][:10],         None),
            (f"{r['qty']:,}",            None),
            (f"{r['buy_price']:,}",      None),
            (f"{r['sell_price']:,}",     None),
            (f"{r['buy_amt']:,}",        None),
            (f"{r['sell_amt']:,}",       None),
            (signed(r["net"]),           nc),
            (pct_s(r["pct"]),            pc),
            (f"{r['fee']:,}",            None),
            (f"{r['tax']:,}",            None),
        ]
        rows.append(make_data_row(TODAY_COLS, values))
    return rows


def build_today_total(today_recs):
    if not today_recs:
        return None, {}
    net_t   = sum(r["net"]      for r in today_recs)
    fee_t   = sum(r["fee"]      for r in today_recs)
    tax_t   = sum(r["tax"]      for r in today_recs)
    gross_t = sum(r["gross"]    for r in today_recs)
    buy_t   = sum(r["buy_amt"]  for r in today_recs)
    sell_t  = sum(r["sell_amt"] for r in today_recs)
    win     = sum(1 for r in today_recs if r["net"] >= 0)
    cnt     = len(today_recs)
    wr      = gross_t / buy_t * 100 if buy_t else 0
    nc = R if net_t >= 0 else B
    pc = R if wr >= 0 else B

    return (
        make_data_row(TODAY_COLS, [
            ("합  계",               BLD),
            (f"{win}승 {cnt-win}패", None),
            ("",                     None),
            (f"승률{win/cnt*100:.0f}%", None),
            ("",                     None),
            ("",                     None),
            ("",                     None),
            (f"{buy_t:,}",           None),
            (f"{sell_t:,}",          None),
            (signed(net_t),          nc),
            (pct_s(wr),              pc),
            (f"{fee_t:,}",           None),
            (f"{tax_t:,}",           None),
        ]),
        {"buy": buy_t, "sell": sell_t, "net": net_t,
         "fee": fee_t, "tax": tax_t, "gross": gross_t,
         "win": win, "cnt": cnt, "wr": wr}
    )


# ══════════════════════════════════════════
# TUI 렌더링
# ══════════════════════════════════════════

def render_screen(tab, scroll, rows, cols, total_row, summary, today_str, h, w):
    lines = []

    # ── 탭바 ──────────────────────────────────────────────
    tab0 = (" 일별 실현손익 ", 0)
    tab1 = (" 당일 손익 상세 ", 1)
    tab_line = ""
    for label, idx in [tab0, tab1]:
        if idx == tab:
            tab_line += REV + BLD + label + RST + "  "
        else:
            tab_line += GR + label + RST + "  "
    today_label = f"  {today_str} ({dow(today_str)})"
    tab_line += GR + today_label + RST
    lines.append(tab_line)
    lines.append(GR + "─" * 78 + RST)

    # ── 요약 헤더 ─────────────────────────────────────────
    s = summary
    if s:
        nc = R if s["net"] >= 0 else B
        pc = R if s.get("wr", 0) >= 0 else B
        lines.append(
            f"  총매수 {W}{s['buy']:>13,}{RST}원   총매도 {W}{s['sell']:>13,}{RST}원   "
            f"실현손익 {nc}{BLD}{signed(s['net']):>11}{RST}원   "
            f"총수익률 {pc}{BLD}{pct_s(s.get('wr',0)):>7}{RST}"
        )
        lines.append(
            f"  수수료 {GR}{s['fee']:>9,}{RST}원   세금합 {GR}{s['tax']:>9,}{RST}원   "
            f"거래 {s['cnt']}건 ({s['win']}승 {s['cnt']-s['win']}패)"
        )
    else:
        lines.append("  데이터 없음")
        lines.append("")
    lines.append(GR + "─" * 78 + RST)

    # ── 테이블 헤더 ───────────────────────────────────────
    lines.append(GR + make_top(cols) + RST)
    lines.append(make_header_row(cols))
    lines.append(GR + make_sep(cols) + RST)

    FIXED = len(lines) + 2  # 고정 헤더 행 수 (+ 하단 합계+상태바)
    viewable = max(1, h - FIXED - 1)

    # ── 스크롤 데이터 행 ──────────────────────────────────
    max_scroll = max(0, len(rows) - viewable)
    sc = min(scroll[0], max_scroll)
    scroll[0] = sc

    visible = rows[sc: sc + viewable]
    for row_line in visible:
        lines.append(row_line)
    for _ in range(viewable - len(visible)):
        lines.append(GR + '│' + RST + ' ' * (sum(c[1]+3 for c in cols) - 1) + GR + '│' + RST)

    # ── 합계 행 ───────────────────────────────────────────
    if total_row:
        lines.append(GR + make_sep(cols, '├', '┼', '┤') + RST)
        lines.append(total_row)
    lines.append(GR + make_bot(cols) + RST)

    # ── 출력 ──────────────────────────────────────────────
    clr_scr()
    print(HID, end='', flush=True)
    for line in lines:
        print(line)

    # ── 상태바 ────────────────────────────────────────────
    status = (f" ↑↓ PgUp PgDn: 스크롤  Tab: 탭전환  q: 종료  "
              f"({sc+1}~{min(sc+viewable, len(rows))}/{len(rows)}행) ")
    print(REV + status.ljust(79) + RST, end='', flush=True)


# ══════════════════════════════════════════
# 키보드 입력 (Windows msvcrt)
# ══════════════════════════════════════════

def get_key():
    key = msvcrt.getwch()
    if key in ('\x00', '\xe0'):
        key2 = msvcrt.getwch()
        return 'SPECIAL_' + key2
    return key


# ══════════════════════════════════════════
# 메인 TUI 루프
# ══════════════════════════════════════════

def run_tui(records):
    today_str  = date.today().isoformat()
    by_date    = defaultdict(list)
    today_recs = []
    for r in records:
        by_date[r["date"]].append(r)
        if r["date"] == today_str:
            today_recs.append(r)

    daily_rows  = build_daily_rows(by_date)
    daily_total, daily_sum = build_daily_total(by_date)

    today_rows  = build_today_rows(today_recs)
    today_total, today_sum = build_today_total(today_recs)

    tab    = 0
    scroll = [0]   # mutable for inner use

    try:
        while True:
            h, w = term_size()

            if tab == 0:
                render_screen(tab, scroll, daily_rows, DAILY_COLS,
                              daily_total, daily_sum, today_str, h, w)
                viewable = max(1, h - 10)
                max_sc   = max(0, len(daily_rows) - viewable)
            else:
                render_screen(tab, scroll, today_rows, TODAY_COLS,
                              today_total, today_sum, today_str, h, w)
                viewable = max(1, h - 10)
                max_sc   = max(0, len(today_rows) - viewable)

            key = get_key()

            if key in ('q', 'Q', '\x1b'):
                break
            elif key == '\t':
                tab = 1 - tab
                scroll[0] = 0
            elif key == 'SPECIAL_H':   # ↑
                scroll[0] = max(0, scroll[0] - 1)
            elif key == 'SPECIAL_P':   # ↓
                scroll[0] = min(max_sc, scroll[0] + 1)
            elif key == 'SPECIAL_I':   # PgUp
                scroll[0] = max(0, scroll[0] - viewable)
            elif key == 'SPECIAL_Q':   # PgDn
                scroll[0] = min(max_sc, scroll[0] + viewable)
            elif key == 'SPECIAL_G':   # Home
                scroll[0] = 0
            elif key == 'SPECIAL_O':   # End
                scroll[0] = max_sc

    finally:
        print(SHW, end='', flush=True)
        clr_scr()


def print_tables(records):
    """--print 모드: TUI 없이 두 테이블을 바로 출력"""
    today_str = date.today().isoformat()
    by_date   = defaultdict(list)
    today_recs = []
    for r in records:
        by_date[r["date"]].append(r)
        if r["date"] == today_str:
            today_recs.append(r)

    # ── 일별 실현손익 ──────────────────────────────────────
    print()
    print(f"  {BLD}[ 일별 실현손익 ]{RST}")
    print(GR + make_top(DAILY_COLS) + RST)
    print(make_header_row(DAILY_COLS))
    print(GR + make_sep(DAILY_COLS) + RST)
    for row in build_daily_rows(by_date):
        print(row)
    total_row, _ = build_daily_total(by_date)
    print(GR + make_sep(DAILY_COLS, '├', '┼', '┤') + RST)
    print(total_row)
    print(GR + make_bot(DAILY_COLS) + RST)

    # ── 당일 손익 상세 ─────────────────────────────────────
    print()
    print(f"  {BLD}[ 당일 손익 상세  {today_str} ({dow(today_str)}) ]{RST}")
    print(GR + make_top(TODAY_COLS) + RST)
    print(make_header_row(TODAY_COLS))
    print(GR + make_sep(TODAY_COLS) + RST)
    if today_recs:
        for row in build_today_rows(today_recs):
            print(row)
        total_row2, s2 = build_today_total(today_recs)
        print(GR + make_sep(TODAY_COLS, '├', '┼', '┤') + RST)
        print(total_row2)
    else:
        print(GR + '│' + RST + '  오늘 매도 내역 없음' + ' ' * 60 + GR + '│' + RST)
    print(GR + make_bot(TODAY_COLS) + RST)
    print()


def main():
    print_mode = "--print" in sys.argv
    records = collect_all_records()
    if not records:
        print("\n  수익 데이터 없음 — 자동매매 실행 후 매도가 발생해야 기록됩니다.\n")
        return
    if print_mode:
        print_tables(records)
    else:
        run_tui(records)


if __name__ == "__main__":
    main()
