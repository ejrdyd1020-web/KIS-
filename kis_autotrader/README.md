# KIS 자동매매 프로그램

한국투자증권(KIS) Open API를 이용한 단타 자동매매 프로그램입니다.

## 📁 폴더 구조

```
kis_autotrader/
├── api/
│   ├── balance.py       # 잔고 조회
│   ├── chart.py         # 분봉 조회 / MA40 계산
│   ├── order.py         # 매수/매도 주문
│   └── price.py         # 현재가 / 거래량 순위 조회
├── monitor/
│   └── position.py      # 보유 포지션 모니터링 (손절/익절/강제청산)
├── strategy/
│   └── condition.py     # 매수 조건 검색 및 필터링
├── utils/
│   └── logger.py        # 로깅 설정
├── auth.py              # KIS API 토큰 발급/관리
├── config.py            # 설정값 (예산, 손익 기준 등)
├── main.py              # 실제 자동매매 실행
├── simulate.py          # 시뮬레이션 모드 (가상 매매)
├── backtest.py          # 백테스트
├── backtest_chart.py    # 백테스트 차트 생성
└── .env                 # API 키 (git 제외)
```

## ⚙️ 설치

```bash
pip install requests python-dotenv
```

## 🔑 환경변수 설정

`.env` 파일을 생성하고 아래 내용을 입력하세요:

```
KIS_APP_KEY=your_app_key
KIS_APP_SECRET=your_app_secret
KIS_CANO=your_account_number
KIS_ACNT_PRDT_CD=01
KIS_IS_REAL=false
```

> `KIS_IS_REAL=false` → 모의투자 / `KIS_IS_REAL=true` → 실전투자

## 🚀 실행

```bash
# 실제 자동매매 (모의/실전)
python main.py

# 시뮬레이션 모드 (주문 없음)
python simulate.py

# 백테스트
python backtest.py

# 백테스트 차트
python backtest_chart.py
```

## 📊 매매 전략

### 매수 조건
| 조건 | 기준 |
|------|------|
| 등락률 | +2.0% ~ +10.0% |
| 전일 거래대금 | 500억 이상 |
| 거래량 급증 | 전일 대비 2배 OR 1분봉 500% |
| 52주 신고가 | 98% 이상 |
| 시가총액 | 2,500억 이상 |
| 매수호가 비율 | 55% 이상 |

### 매도 조건
| 조건 | 기준 |
|------|------|
| 익절 | +5.0% |
| 고정 손절 | -3.0% |
| MA40 손절 | 1분봉 MA40 이탈 |
| 체결강도 손절 | 100% 미만 1분 유지 |
| 강제 청산 | 15:20 |

### 자금 관리
- 총 예산: 10,000,000원
- 최대 보유 종목: 3개
- 종목당 투자금: 3,333,333원

## ⚠️ 주의사항

- `.env` 파일은 절대 깃허브에 올리지 마세요
- 실전 투자 전 반드시 모의투자로 충분히 테스트하세요
- 투자 손실에 대한 책임은 본인에게 있습니다
