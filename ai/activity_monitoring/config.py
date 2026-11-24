import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

USER_NAME = os.getenv("USER_NAME", "Shreeshanth")
