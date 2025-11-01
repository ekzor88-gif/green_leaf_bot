import os
from dotenv import load_dotenv, find_dotenv

# найдём .env начиная с текущей папки и выше
dotenv_path = find_dotenv(usecwd=True)
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    print(f"⚠️ .env не найден. Текущая папка: {os.getcwd()}")

# Поддержим оба названия переменной:
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1mwPaoPZce1BFazHaPFkH1nyLcei9iJpAeNe6lhRVL3M/edit?usp=sharing"

missing = []
if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN (или BOT_TOKEN)")
if not SUPABASE_URL:  missing.append("SUPABASE_URL")
if not SUPABASE_KEY:  missing.append("SUPABASE_KEY")
if not OPENAI_API_KEY: missing.append("OPENAI_API_KEY")

if missing:
    raise ValueError("❌ Не найдены переменные в .env: " + ", ".join(missing))
