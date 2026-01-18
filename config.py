import os
from dotenv import load_dotenv, find_dotenv

# найдём .env начиная с текущей папки и выше
# Если файла нет, load_dotenv просто ничего не сделает (это нормально для сервера)
# Попытка загрузить .env (для локального запуска)
# На сервере файла нет, это нормально.
load_dotenv(find_dotenv(usecwd=True))

print("🔧 Загрузка конфигурации...")

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
    print("----------------------------------------------------------------")
    print(f"❌ ОШИБКА: Не найдены переменные окружения: {', '.join(missing)}")
    print(f"📂 Текущая папка: {os.getcwd()}")
    print("🔍 СПИСОК ДОСТУПНЫХ ПЕРЕМЕННЫХ (ключи):")
    # Выводим только названия переменных, чтобы не слить пароли в логи
    for key in os.environ.keys():
        print(f" - {key}")
    print("----------------------------------------------------------------")
    raise ValueError("Проверьте настройки 'Variables' (Переменные) в панели управления хостинга!")

print("✅ [CONFIG] Конфигурация успешно проверена.")
