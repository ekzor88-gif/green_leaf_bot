import os
from dotenv import load_dotenv, find_dotenv

# найдём .env начиная с текущей папки и выше
# Если файла нет, load_dotenv просто ничего не сделает (это нормально для сервера)
# Попытка загрузить .env (для локального запуска)
# На сервере файла нет, это нормально.
load_dotenv(find_dotenv(usecwd=True))

print("[CONFIG] Loading configuration...")

# Поддержим оба названия переменной:
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Номер "Дефолтного менеджера" (Владельца бота)
DEFAULT_MANAGER_PHONE = "77012706305" 

# ID администраторов с иммунитетом к блокировкам (через запятую)
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "1025315242,575300542").split(",") if x.strip()]

# Реферальный код Надежды по умолчанию для органических пользователей
DEFAULT_PARTNER_CODE = "NadinGreenleaf"

GOOGLE_SHEET_URL = "https://drive.google.com/file/d/11Q-jcD1z6jnHwaxAqN69YpFiCeeKKC5M/view?usp=sharing"

# 💡 Ссылка на видео-инструкцию (Google Drive, YouTube или др.)
# Если оставить пустой (""), ссылка в приветствии отображаться не будет.
VIDEO_INSTRUCTION_URL = "https://drive.google.com/file/d/1ptS9_SCRPk8E9KSojGyZ4LRGu9gdmRDm/view?usp=sharing" 

missing = []
if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN (или BOT_TOKEN)")
if not SUPABASE_URL:  missing.append("SUPABASE_URL")
if not SUPABASE_KEY:  missing.append("SUPABASE_KEY")
if not OPENAI_API_KEY: missing.append("OPENAI_API_KEY")

if missing:
    print("----------------------------------------------------------------")
    print(f"[ERROR] Environment variables not found: {', '.join(missing)}")
    print(f"Current folder: {os.getcwd()}")
    print("List of available keys:")
    # Выводим только названия переменных, чтобы не слить пароли в логи
    for key in os.environ.keys():
        print(f" - {key}")
    print("----------------------------------------------------------------")
    raise ValueError("Проверьте настройки 'Variables' (Переменные) в панели управления хостинга!")

print("[SUCCESS] Configuration checked successfully.")
