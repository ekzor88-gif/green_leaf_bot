import os
from dotenv import load_dotenv, find_dotenv

# найдём .env начиная с текущей папки и выше
# Если файла нет, load_dotenv просто ничего не сделает (это нормально для сервера)
load_dotenv(find_dotenv(usecwd=True))

# Поддержим оба названия переменной:
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Номер "Дефолтного менеджера" (Владельца бота)
DEFAULT_MANAGER_PHONE = "77012706305" 

GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1mwPaoPZce1BFazHaPFkH1nyLcei9iJpAeNe6lhRVL3M/edit?usp=sharing"

missing = []
if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN (или BOT_TOKEN)")
if not SUPABASE_URL:  missing.append("SUPABASE_URL")
if not SUPABASE_KEY:  missing.append("SUPABASE_KEY")
if not OPENAI_API_KEY: missing.append("OPENAI_API_KEY")

if missing:
    print(f"⚠️ .env не найден или пуст. Текущая папка: {os.getcwd()}")
    raise ValueError("❌ Не найдены переменные в .env: " + ", ".join(missing))
