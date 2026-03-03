# ============================================================
#  backtest_chart.py  –  백테스트 결과 HTML 차트 생성
# ============================================================

from dotenv import load_dotenv
import os, json
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import requests
from auth import get_access_token, get_headers, get_base_url
from config import STOP_LOSS_PCT, TAKE_PROFIT_PCT

get_access_token()
BASE_URL = get_base_url()

TARGETS     = [
    {"code": "000660", "name": "SK하이닉스"},
    
    
]
TARGET_DATE = "20260225"
BUY_TIME    = "0900"


def get_minute_candles(stock_code, date):
    all_candles = []
    end_time = "160000"
    while True:
        res = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=get_headers("FHKST03010200"),
            params={
                "fid_etc_cls_code"      : "",
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd"        : stock_code,
                "fid_input_hour_1"      : end_time,
                "fid_pw_data_incu_yn"   : "Y",
            }, timeout=10,
        )
        if res.status_code != 200:
            break
        data = res.json()
        if data.get("rt_cd") != "0":
            break
        candles = data.get("output2", [])
        if not candles:
            break
        day_candles = [
            {
                "time"  : c.get("stck_cntg_hour", ""),
                "open"  : int(c.get("stck_oprc", 0)),
                "high"  : int(c.get("stck_hgpr", 0)),
                "low"   : int(c.get("stck_lwpr", 0)),
                "close" : int(c.get("stck_prpr", 0)),
                "volume": int(c.get("cntg_vol", 0)),
                "date"  : c.get("stck_bsop_date", ""),
            }
            for c in candles if c.get("stck_bsop_date", "") == date
        ]
        all_candles.extend(day_candles)
        if not any(c.get("stck_bsop_date") == date for c in candles):
            break
        last_time = candles[-1].get("stck_cntg_hour", "")
        if not last_time or last_time >= end_time:
            break
        end_time = last_time

    all_candles.sort(key=lambda x: x["time"])
    return all_candles


def calc_ma40(candles, idx):
    if idx < 40:
        return None
    closes = [candles[i]["close"] for i in range(idx - 20, idx)]
    return sum(closes) / 40


def run_backtest(code, name, candles):
    if not candles:
        return None
    buy_candle = next((c for c in candles if c["time"] >= BUY_TIME), None)
    if not buy_candle:
        return None

    buy_price   = buy_candle["open"] or buy_candle["close"]
    stop_loss   = int(buy_price * (1 + STOP_LOSS_PCT   / 100))
    take_profit = int(buy_price * (1 + TAKE_PROFIT_PCT / 100))
    buy_idx     = candles.index(buy_candle)

    ma40_line = [calc_ma40(candles, i) for i in range(len(candles))]

    sell_price  = None
    sell_idx    = None
    sell_reason = None

    for i in range(buy_idx + 1, len(candles)):
        c     = candles[i]
        price = c["close"]
        ma20  = ma40_line[i]

        if c["time"] >= "1520":
            sell_price = price; sell_idx = i; sell_reason = "장마감"; break
        if price <= stop_loss:
            sell_price = price; sell_idx = i; sell_reason = f"고정손절"; break
        if price >= take_profit:
            sell_price = price; sell_idx = i; sell_reason = f"익절"; break
        if ma20 and price < ma20:
            sell_price = price; sell_idx = i; sell_reason = f"MA40손절"; break

    if not sell_price:
        sell_price = candles[-1]["close"]
        sell_idx   = len(candles) - 1
        sell_reason = "장마감"

    profit_pct = (sell_price - buy_price) / buy_price * 100

    return {
        "name"       : name,
        "code"       : code,
        "candles"    : candles,
        "ma40_line"  : ma40_line,
        "buy_idx"    : buy_idx,
        "buy_price"  : buy_price,
        "sell_idx"   : sell_idx,
        "sell_price" : sell_price,
        "sell_reason": sell_reason,
        "profit_pct" : profit_pct,
        "stop_loss"  : stop_loss,
        "take_profit": take_profit,
    }


# ── 데이터 수집 ───────────────────────────────────────────────
results = []
for t in TARGETS:
    print(f"[{t['name']}] 분봉 조회 중...")
    candles = get_minute_candles(t["code"], TARGET_DATE)
    print(f"  → {len(candles)}개")
    r = run_backtest(t["code"], t["name"], candles)
    if r:
        results.append(r)

# ── HTML 생성 ─────────────────────────────────────────────────
charts_js = []
for r in results:
    candles   = r["candles"]
    ma40_line = r["ma40_line"]
    labels    = [f"{c['time'][:2]}:{c['time'][2:]}" for c in candles]
    closes    = [c["close"] for c in candles]
    ma20_vals = [round(v, 0) if v else None for v in ma40_line]

    buy_point  = [None] * len(candles)
    sell_point = [None] * len(candles)
    if r["buy_idx"] is not None:
        buy_point[r["buy_idx"]]   = r["buy_price"]
    if r["sell_idx"] is not None:
        sell_point[r["sell_idx"]] = r["sell_price"]

    profit_pct = r["profit_pct"]
    sign       = "+" if profit_pct >= 0 else ""
    color      = "#00e676" if profit_pct >= 0 else "#ff5252"
    reason_color = {
        "익절": "#00e676", "MA40손절": "#ff9800",
        "고정손절": "#ff5252", "장마감": "#90caf9"
    }.get(r["sell_reason"], "#ffffff")

    charts_js.append({
        "name"        : r["name"],
        "code"        : r["code"],
        "labels"      : labels,
        "closes"      : closes,
        "ma40"        : ma20_vals,
        "buy_point"   : buy_point,
        "sell_point"  : sell_point,
        "buy_price"   : r["buy_price"],
        "sell_price"  : r["sell_price"],
        "sell_reason" : r["sell_reason"],
        "profit_pct"  : round(profit_pct, 2),
        "sign"        : sign,
        "color"       : color,
        "reason_color": reason_color,
        "stop_loss"   : r["stop_loss"],
        "take_profit" : r["take_profit"],
    })

charts_json = json.dumps(charts_js, ensure_ascii=False)

html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>백테스트 차트 - {TARGET_DATE[:4]}.{TARGET_DATE[4:6]}.{TARGET_DATE[6:]}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+KR:wght@300;500;700&display=swap');
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    background: #0a0e1a;
    color: #e0e6f0;
    font-family: 'IBM Plex Sans KR', sans-serif;
    min-height: 100vh;
    padding: 32px 24px;
  }}
  .header {{
    text-align: center;
    margin-bottom: 40px;
  }}
  .header h1 {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.4rem;
    font-weight: 600;
    color: #7eb3ff;
    letter-spacing: 2px;
    margin-bottom: 6px;
  }}
  .header p {{
    font-size: 0.8rem;
    color: #4a5568;
    font-family: 'IBM Plex Mono', monospace;
  }}
  .charts-grid {{
    display: flex;
    flex-direction: column;
    gap: 32px;
    max-width: 1100px;
    margin: 0 auto;
  }}
  .chart-card {{
    background: #111827;
    border: 1px solid #1e2d45;
    border-radius: 12px;
    padding: 24px;
  }}
  .card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    padding-bottom: 16px;
    border-bottom: 1px solid #1e2d45;
  }}
  .stock-info {{ display: flex; align-items: center; gap: 14px; }}
  .stock-name {{
    font-size: 1.1rem;
    font-weight: 700;
    color: #e0e6f0;
  }}
  .stock-code {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    color: #4a5568;
    background: #1a2235;
    padding: 2px 8px;
    border-radius: 4px;
  }}
  .trade-info {{
    display: flex;
    gap: 20px;
    align-items: center;
  }}
  .trade-item {{
    text-align: right;
  }}
  .trade-label {{
    font-size: 0.7rem;
    color: #4a5568;
    font-family: 'IBM Plex Mono', monospace;
  }}
  .trade-value {{
    font-size: 0.9rem;
    font-weight: 600;
    font-family: 'IBM Plex Mono', monospace;
  }}
  .profit-badge {{
    padding: 6px 14px;
    border-radius: 6px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1rem;
    font-weight: 600;
  }}
  .reason-badge {{
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 0.72rem;
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 600;
    border: 1px solid;
  }}
  .chart-wrap {{ position: relative; height: 300px; }}
  .legend {{
    display: flex;
    gap: 16px;
    margin-top: 12px;
    flex-wrap: wrap;
  }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.75rem;
    color: #6b7a99;
    font-family: 'IBM Plex Mono', monospace;
  }}
  .legend-dot {{
    width: 10px; height: 10px;
    border-radius: 50%;
  }}
  .legend-line {{
    width: 20px; height: 2px;
  }}
</style>
</head>
<body>
<div class="header">
  <h1>BACKTEST REPORT</h1>
  <p>{TARGET_DATE[:4]}.{TARGET_DATE[4:6]}.{TARGET_DATE[6:]} &nbsp;|&nbsp; 매수: 9시 시가 &nbsp;|&nbsp; 손절: MA40 / {STOP_LOSS_PCT:+.1f}% &nbsp;|&nbsp; 익절: +{TAKE_PROFIT_PCT:.1f}%</p>
</div>
<div class="charts-grid" id="charts"></div>

<script>
const CHARTS_DATA = {charts_json};

function buildChart(r, idx) {{
  const card = document.createElement('div');
  card.className = 'chart-card';

  const profitStyle = r.profit_pct >= 0
    ? 'background:#0d2818;color:#00e676;'
    : 'background:#2a0d0d;color:#ff5252;';

  card.innerHTML = `
    <div class="card-header">
      <div class="stock-info">
        <span class="stock-name">${{r.name}}</span>
        <span class="stock-code">${{r.code}}</span>
        <span class="reason-badge" style="color:${{r.reason_color}};border-color:${{r.reason_color}}20;background:${{r.reason_color}}10">${{r.sell_reason}}</span>
      </div>
      <div class="trade-info">
        <div class="trade-item">
          <div class="trade-label">매수가</div>
          <div class="trade-value" style="color:#7eb3ff">${{r.buy_price.toLocaleString()}}원</div>
        </div>
        <div class="trade-item">
          <div class="trade-label">매도가</div>
          <div class="trade-value" style="color:#e0e6f0">${{r.sell_price.toLocaleString()}}원</div>
        </div>
        <div class="profit-badge" style="${{profitStyle}}">${{r.sign}}${{r.profit_pct.toFixed(2)}}%</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="chart_${{idx}}"></canvas></div>
    <div class="legend">
      <div class="legend-item"><div class="legend-line" style="background:#7eb3ff"></div>종가</div>
      <div class="legend-item"><div class="legend-line" style="background:#f59e0b"></div>MA40</div>
      <div class="legend-item"><div class="legend-dot" style="background:#00e676"></div>매수</div>
      <div class="legend-item"><div class="legend-dot" style="background:#ff5252"></div>매도</div>
      <div class="legend-item"><div class="legend-line" style="background:#ff525250;border-top:1px dashed #ff5252"></div>손절선 (${{r.stop_loss.toLocaleString()}})</div>
      <div class="legend-item"><div class="legend-line" style="background:#00e67650;border-top:1px dashed #00e676"></div>익절선 (${{r.take_profit.toLocaleString()}})</div>
    </div>
  `;
  document.getElementById('charts').appendChild(card);

  const stopArr  = new Array(r.labels.length).fill(r.stop_loss);
  const tpArr    = new Array(r.labels.length).fill(r.take_profit);

  new Chart(document.getElementById(`chart_${{idx}}`), {{
    type: 'line',
    data: {{
      labels: r.labels,
      datasets: [
        {{
          label: '종가',
          data: r.closes,
          borderColor: '#7eb3ff',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.1,
          fill: false,
          order: 3,
        }},
        {{
          label: 'MA40',
          data: r.ma20,
          borderColor: '#f59e0b',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
          spanGaps: true,
          order: 2,
        }},
        {{
          label: '손절선',
          data: stopArr,
          borderColor: '#ff525260',
          borderWidth: 1,
          borderDash: [4,4],
          pointRadius: 0,
          fill: false,
          order: 4,
        }},
        {{
          label: '익절선',
          data: tpArr,
          borderColor: '#00e67660',
          borderWidth: 1,
          borderDash: [4,4],
          pointRadius: 0,
          fill: false,
          order: 4,
        }},
        {{
          label: '매수',
          data: r.buy_point,
          borderColor: '#00e676',
          backgroundColor: '#00e676',
          pointRadius: r.buy_point.map(v => v ? 8 : 0),
          pointStyle: 'triangle',
          showLine: false,
          order: 1,
        }},
        {{
          label: '매도',
          data: r.sell_point,
          borderColor: '#ff5252',
          backgroundColor: '#ff5252',
          pointRadius: r.sell_point.map(v => v ? 8 : 0),
          pointStyle: 'triangle',
          rotation: 180,
          showLine: false,
          order: 1,
        }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#1a2235',
          borderColor: '#2a3a55',
          borderWidth: 1,
          callbacks: {{
            label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y?.toLocaleString()}}원`
          }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{
            color: '#3a4a66',
            maxTicksLimit: 12,
            font: {{ family: 'IBM Plex Mono', size: 10 }}
          }},
          grid: {{ color: '#111827' }}
        }},
        y: {{
          ticks: {{
            color: '#3a4a66',
            font: {{ family: 'IBM Plex Mono', size: 10 }},
            callback: v => v.toLocaleString()
          }},
          grid: {{ color: '#1a2235' }}
        }}
      }}
    }}
  }});
}}

CHARTS_DATA.forEach((r, i) => buildChart(r, i));
</script>
</body>
</html>"""

out_path = os.path.join(os.path.dirname(__file__), "backtest_chart.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\n✅ 차트 생성 완료: backtest_chart.html")
print("브라우저에서 열어보세요!")
