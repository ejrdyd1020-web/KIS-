from dotenv import load_dotenv
import json, os, sys
load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from auth import get_access_token
get_access_token()

from api.ohlcv import fetch_prev_ohlcv_single
result = fetch_prev_ohlcv_single("005930")
print(json.dumps(result, ensure_ascii=False, indent=2))
