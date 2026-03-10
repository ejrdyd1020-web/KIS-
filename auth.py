# ============================================================
#  auth.py  –  KIS API 인증 토큰 발급 및 자동 갱신
# ============================================================

import requests
import json
import time
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

APP_KEY    = os.environ["KIS_APP_KEY"]
APP_SECRET = os.environ["KIS_APP_SECRET"]
IS_REAL    = os.environ.get("KIS_IS_REAL", "false").lower() == "true"

BASE_URL = (
    "https://openapi.koreainvestment.com:9443"
    if IS_REAL else
    "https://openapivts.koreainvestment.com:29443"
)

import pathlib

# 토큰 캐시 (메모리 + 파일)
_token_cache = {
    "access_token"  : None,
    "expires_at"    : None,   # datetime
    "ws_token"      : None,
}

_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "token_cache.json")

def _load_token_file():
    """파일에서 토큰 캐시 로드"""
    try:
        if not os.path.exists(_TOKEN_FILE):
            return
        with open(_TOKEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        expires_at = datetime.strptime(data["expires_at"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() < expires_at - timedelta(minutes=5):
            _token_cache["access_token"] = data["access_token"]
            _token_cache["expires_at"]   = expires_at
    except Exception:
        pass

def _save_token_file():
    """토큰 캐시를 파일에 저장"""
    try:
        pathlib.Path(os.path.dirname(_TOKEN_FILE)).mkdir(parents=True, exist_ok=True)
        with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "access_token": _token_cache["access_token"],
                "expires_at"  : _token_cache["expires_at"].strftime("%Y-%m-%d %H:%M:%S"),
            }, f)
    except Exception:
        pass

# 프로세스 시작 시 파일 캐시 로드
_load_token_file()


def get_access_token(force_refresh: bool = False) -> str:
    """
    액세스 토큰 반환.
    만료 5분 전이면 자동 재발급.
    """
    now = datetime.now()
    cached = _token_cache["access_token"]
    expires = _token_cache["expires_at"]

    if not force_refresh and cached and expires and now < expires - timedelta(minutes=5):
        return cached  # 캐시 사용

    # 신규 발급
    res = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey"    : APP_KEY,
            "appsecret" : APP_SECRET,
        },
        timeout=10,
    )
    res.raise_for_status()
    data = res.json()

    token = data.get("access_token")
    if not token:
        raise ValueError(f"토큰 발급 실패: {data}")

    # 만료 시간 파싱 (KIS는 "YYYY-MM-DD HH:MM:SS" 형식 반환)
    expires_str = data.get("access_token_token_expired", "")
    try:
        expires_at = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        expires_at = now + timedelta(hours=24)

    _token_cache["access_token"] = token
    _token_cache["expires_at"]   = expires_at
    _save_token_file()  # 파일에도 저장

    print(f"[AUTH] ✅ 토큰 발급 성공 (만료: {expires_at.strftime('%H:%M:%S')})")
    return token


def get_ws_token() -> str:
    """
    웹소켓 접속키 발급 (실시간 시세용).
    """
    if _token_cache["ws_token"]:
        return _token_cache["ws_token"]

    res = requests.post(
        f"{BASE_URL}/oauth2/Approval",
        json={
            "grant_type": "client_credentials",
            "appkey"    : APP_KEY,
            "secretkey" : APP_SECRET,
        },
        timeout=10,
    )
    res.raise_for_status()
    data = res.json()

    ws_token = data.get("approval_key")
    if not ws_token:
        raise ValueError(f"웹소켓 토큰 발급 실패: {data}")

    _token_cache["ws_token"] = ws_token
    print(f"[AUTH] ✅ 웹소켓 토큰 발급 성공")
    return ws_token


def get_headers(tr_id: str, extra: dict = None) -> dict:
    """
    API 공통 헤더 생성.
    tr_id: KIS 거래 ID (예: TTTC8434R)
    """
    headers = {
        "content-type" : "application/json",
        "authorization": f"Bearer {get_access_token()}",
        "appkey"       : APP_KEY,
        "appsecret"    : APP_SECRET,
        "tr_id"        : tr_id,
    }
    if extra:
        headers.update(extra)
    return headers


def get_base_url() -> str:
    return BASE_URL


def get_account() -> tuple[str, str]:
    """계좌번호, 계좌상품코드 반환"""
    cano         = os.environ["KIS_CANO"]
    acnt_prdt_cd = os.environ.get("KIS_ACNT_PRDT_CD", "01")
    return cano, acnt_prdt_cd


# ── 테스트 ────────────────────────────────────────────────────
if __name__ == "__main__":
    token = get_access_token()
    print(f"토큰 앞 20자: {token[:20]}...")
    ws    = get_ws_token()
    print(f"웹소켓키 앞 20자: {ws[:20]}...")
