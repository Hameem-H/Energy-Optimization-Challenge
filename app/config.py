import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
PORT = int(os.getenv("PORT", 8000))
