# ============================================================
#  config.py  –  자동매매 전략 설정
# ============================================================

# ── 투자 금액 설정 ────────────────────────────────────────────
TOTAL_BUDGET        = 10_000_000   # 1회 운용 총 예산 (원)
MAX_POSITIONS       = 6            # 최대 동시 보유 종목 수 (A전략 3 + B전략 3)
ORDER_AMOUNT        = TOTAL_BUDGET // 6  # 종목당 기본 투자금액 (동적 배분 fallback)

# ── 손절 / 익절 설정 ─────────────────────────────────────────
#  ※ 전략별 분리 적용 — BREAKOUT / REVERSION 섹션 참고
#  하위 호환용 기본값 (투 트랙 전략 미적용 구간 fallback)
STOP_LOSS_PCT       = -3.0        # 고정 손절 fallback
TAKE_PROFIT_PCT     = 5.0         # 익절 fallback (A전략 기준)

# ── 거래 비용 설정 ────────────────────────────────────────────
#   수수료: 매수 0.015% + 매도 0.015%
#   거래세: 코스피 0.18%, 코스닥 0.18% (2024년 기준)
#   총 비용: 약 0.21% (수수료 0.03% + 세금 0.18%)
TRADE_COST = {
    "buy_fee"   : 0.00015,   # 매수 수수료
    "sell_fee"  : 0.00015,   # 매도 수수료
    "sell_tax"  : 0.0018,    # 거래세 (코스피/코스닥 동일)
}
TOTAL_TRADE_COST_PCT = (
    TRADE_COST["buy_fee"] + TRADE_COST["sell_fee"] + TRADE_COST["sell_tax"]
) * 100   # 약 0.21 (%)

# ── 장 시간 설정 ─────────────────────────────────────────────
MARKET_OPEN         = "09:00"
MARKET_CLOSE        = "15:20"
CONDITION_SCAN_END  = "14:30"

# ── 모니터링 주기 ────────────────────────────────────────────
PRICE_CHECK_SEC     = 5

# ── 기본 조건 검색 설정 ───────────────────────────────────────
# HTS 조건식 기준: 등락률 7~25%, 시가총액 100억 이상
CONDITION = {
    "min_volume"        : 100_000,
    "min_price"         : 1_000,       # 1,000원 이상 (동전주 제외)
    "max_price"         : 99_999_999,
    "min_change_rate"   : 7.0,
    "max_change_rate"   : 25.0,
}

# ── 고급 필터 조건 ────────────────────────────────────────────
ADVANCED_FILTER = {
    # 거래량 급증 (OR 조건 - 둘 중 하나만 만족해도 통과)
    "volume_surge_ratio"    : 1.3,    # 전일 대비 누적거래량 (2.0 → 1.3, 저변동성 장세 대응)
    "min_volume_ratio_1min" : 500.0,  # 직전 1분봉 대비 현재 1분봉 거래량 500% 이상

    # 시가총액 (억원)
    "min_market_cap"        : 100,
    "max_market_cap"        : 99_999_999,

    # 거래대금 (억원)
    "min_trade_amount"      : 150,    # 300 → 150억, 종목 풀 확대

    # 당일 과열 제외
    "max_day_change_rate"   : 25.0,

    # 체결강도
    "min_execution_strength": 100.0,  # 체결강도 100% 이상
}

# ── 트레일링 스탑 설정 ───────────────────────────────────────
TRAILING_STOP = {
    "min_profit_pct" : 3.0,   # 수익이 3% 이상 났을 때부터 작동
    "drop_pct"       : 2.0,   # 고점 대비 2% 하락 시 청산
}

# ── 스토캐스틱 청산 설정 ─────────────────────────────────────
STOCH_EXIT = {
    "k_period"      : 12,    # Fast %K 기간
    "smooth_period" : 5,     # Slow %K 스무딩
    "d_period"      : 5,     # %D 기간
    "overbought"    : 80,    # 과열권 기준 (80 이상에서 데드크로스 시 청산)
}

# ── 일일 최대 손실 한도 ──────────────────────────────────────
#  하루 실현손실이 한도를 초과하면 stop_event를 세팅하여 자동 중단
#  TOTAL_BUDGET 기준 5% = 500,000원
DAILY_LOSS_LIMIT = {
    "enabled"    : True,
    "max_loss_pct": 5.0,                              # 총 예산 대비 손실 허용 비율 (%)
    "max_loss_amt": int(TOTAL_BUDGET * 0.05),         # 실제 금액 (500,000원)
}

# ── 시장 국면 필터 (1분봉 MA120 기준) ────────────────────────
#  - 매매 중인 종목의 현재가가 1분봉 MA120 위 → 상승장 → 매매 유지
#  - MA120 이탈 시 → 즉시 전량 시장가 손절 + 신규 매매 중단
#  - MA120 회복 시 → 매매 재개
MA120_MARKET_FILTER = {
    "enabled"           : True,
    "candle_interval"   : 1,     # 1분봉
    "ma_period"         : 120,   # 120이평
}

# ============================================================
#  투 트랙 전략별 파라미터
# ============================================================

# ── 전략 A: BREAKOUT  (09:00 ~ 09:10) ────────────────────────
#   공격형 · 장 초반 주도주 포착 · 속도 우선
BREAKOUT = {
    # 매수 조건
    "start_time"        : "09:00",
    "end_time"          : "09:10",
    "min_change_rate"   : 3.0,    # 등락률 하한 (장전 갭 기준 완화)
    "max_change_rate"   : 25.0,   # 등락률 상한
    "volume_surge_ratio": 3.0,    # 전일 대비 거래량 3배 이상 (강한 모멘텀)
    "exec_strength_max"  : 120.0, # 체결강도 만점 기준 (이상이면 30점 만점)
    "change_rate_max"    : 15.0,  # 등락률 만점 기준 (이상이면 30점 만점)
    "ma_filter"         : False,  # MA 필터 OFF (장초반 캔들 부족)
    "stoch_filter"      : False,  # 스토캐스틱 OFF (장초반 신호 불안정)
    "scan_interval_sec" : 5,      # 실시간 스캔 (5초 간격)

    # 손절 / 익절
    "stop_loss_pct"     : -3.0,   # 고정 손절 (ATR 동적손절 적용 예정 — 5주차)
    "take_profit_pct"   : 5.0,    # 고정 익절
    "trailing_stop"     : {
        "min_profit_pct": 3.0,    # 수익 3% 이상 시 트레일링 작동
        "drop_pct"      : 2.0,    # 고점 대비 2% 하락 시 청산
    },
}

# ── 전략 B: REVERSION  (09:10 ~ 15:20) ───────────────────────
#   방어형 · 눌림목 반등 · 스토캐스틱 + 이평 정배열 기반
REVERSION = {
    # 매수 조건
    "start_time"        : "09:10",
    "end_time"          : "15:20",
    "min_change_rate"   : 1.5,    # 등락률 하한 (3.0 → 1.5, 종목 풀 확대)
    "max_change_rate"   : 20.0,   # 등락률 상한 (15.0 → 20.0, 과열 구간 완화)
    "volume_surge_ratio": 1.3,    # 전일 대비 거래량 (2.0 → 1.3, 저변동성 장세 대응)
    "ma_filter"         : True,   # MA20 / MA60 / MA120 정배열 확인
    "stoch_filter"      : True,   # 스토캐스틱 침체권 골든크로스 필수
    "scan_interval_sec" : 30,     # 30초 간격 스캔
    "split_buy"         : False,  # 분할 매수 — 미구현 (단일 진입으로 운영)

    # 손절 / 익절  — A보다 타이트하게 설정
    "stop_loss_pct"     : -1.5,   # 고정 손절 (R:R = 1:2 기준)
    "take_profit_pct"   : 3.0,    # 고정 익절 (짧은 순환매)
    "trailing_stop"     : {
        "min_profit_pct": 2.0,    # 수익 2% 이상 시 트레일링 작동 (더 이른 보호)
        "drop_pct"      : 1.5,    # 고점 대비 1.5% 하락 시 청산
    },
}

# ── 전략 타입 상수 ────────────────────────────────────────────
STRATEGY_BREAKOUT  = "BREAKOUT"
STRATEGY_REVERSION = "REVERSION"
STRATEGY_HALT      = "HALT"      # 장외 시간 / 손실 한도 초과

# ── 시장 국면별 자금 배분 비율 ────────────────────────────────
#   BULL (코스피+코스닥 MA5>MA20): BREAKOUT 80% / REVERSION 20%
#   BEAR (하나라도 보합/하락)     : BREAKOUT 20% / REVERSION 80%
MARKET_PHASE = {
    "bull_threshold"      : 0.5,   # 지수 평균 등락률 +0.5% 이상 = BULL
    "bull_breakout_ratio" : 0.8,   # BULL 시 BREAKOUT 자금 비율
    "bear_breakout_ratio" : 0.2,   # BEAR 시 BREAKOUT 자금 비율
    "max_per_strategy"    : 3,     # 전략별 최대 보유 종목 수
    "ma_short"            : 5,     # 단기 이평 기간 (api/index.py에서 참조)
    "ma_long"             : 20,    # 장기 이평 기간 (api/index.py에서 참조)
}
