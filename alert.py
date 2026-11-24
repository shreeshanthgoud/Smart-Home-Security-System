import requests
from config import CHAT_ID, TOKEN


url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

data = {
    "chat_id": CHAT_ID,
    "text": "⚠️ Test Alert Working"
}

requests.post(url, data=data)