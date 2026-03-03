from dotenv import load_dotenv
import os, requests, json
load_dotenv()

APP_KEY    = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")

print(f"앱키 앞 10자: {APP_KEY[:10]}")
print(f"시크릿 앞 10자: {APP_SECRET[:10]}")

res = requests.post(
    "https://openapivts.koreainvestment.com:29443/oauth2/tokenP",
    headers={"content-type": "application/json"},
    data=json.dumps({
        "grant_type"   : "client_credentials",
        "appkey"       : APP_KEY,
        "appsecret"    : APP_SECRET,
    })
)

print(f"\nHTTP 상태코드: {res.status_code}")
print(f"응답 내용: {res.text}")
