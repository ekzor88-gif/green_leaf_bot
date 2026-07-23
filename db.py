from supabase import create_client, ClientOptions
from openai import OpenAI
import config
import logging
import asyncio 
from typing import Optional
from datetime import datetime, timezone # 💡 Для проверки даты подписки

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

print("[DB] Connecting to Supabase...")
# 💡 Инициализация синхронных клиентов с увеличенным таймаутом
# Это нужно, чтобы "холодный старт" базы на бесплатном тарифе не вызывал ошибку.
options = ClientOptions(postgrest_client_timeout=30)
supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY, options=options)
print("[DB] Supabase client created successfully.")

print("[DB] Connecting to OpenAI...")
openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

# 💡 ОПТИМИЗАЦИЯ: Выносим стоп-слова в константу, чтобы не создавать set каждый раз
STOPWORDS = {
    "с", "в", "на", "за", "из", "для", "от", "по", "у", "о", "без", "и", "а", "но",
    "быть", "весь", "этот", "который", "мой", "наш", "ваш", "как", "где", "сколько",
    "есть", "хочу", "нужен", "нужна", "нужно", "купить", "ищу", "найти", "подскажи", "скажи", "цена", "стоимость",
    "чем", "содержится", "состав", "какой", "какие", "при", "помогает"
}

# ==============================================================================
# 1. ФУНКЦИИ РАБОТЫ С БАЗОЙ ДАННЫХ (С КОРРЕКЦИЕЙ ID И КОНТЕКСТА)
# ==============================================================================

def upsert_user(user_id: int, first_name: str, last_name: str, username: str):
    """
    Обновляет или создает пользователя.
    Если у пользователя нет привязанного партнера, автоматически привязывает его к Надежде.
    """
    try:
        # 1. Upsert основных полей пользователя
        supabase.table("users").upsert({
            "user_id": user_id,
            "first_name": first_name,
            "last_name": last_name,
            "username": username,
        }).execute()
        
        # 2. Проверяем наличие partner_id
        check_res = supabase.table("users").select("partner_id").eq("user_id", user_id).execute()
        if check_res.data and len(check_res.data) > 0:
            if check_res.data[0].get("partner_id") is None:
                # Ищем ID дефолтного партнера (Надежды)
                p_res = supabase.table("partners").select("id").eq("referral_code", config.DEFAULT_PARTNER_CODE).execute()
                if p_res.data and len(p_res.data) > 0:
                    default_partner_id = p_res.data[0]["id"]
                    supabase.table("users").update({"partner_id": default_partner_id}).eq("user_id", user_id).execute()
                    logger.info(f"[DB] Органический пользователь {user_id} автопривязан к Надежде (ID: {default_partner_id})")
    except Exception as e:
        logger.error(f"Ошибка upsert_user: {e}")
        return None


def save_message(user_id: int, role: str, content: str):
    """Сохраняет сообщение в историю диалога. Здесь user_id корректен."""
    return supabase.table("messages").insert({
        "user_id": user_id, "role": role, "content": content
    }).execute()


def get_recent_messages(user_id: int, limit: int = 10):
    """Извлекает последние сообщения пользователя. Здесь user_id корректен."""
    res = (supabase.table("messages")
           .select("*")
           .eq("user_id", user_id)
           .order("id", desc=True)
           .limit(limit)
           .execute())
    return list(reversed(res.data or []))


def save_last_products(user_id: int, products: list):
    """
    СОХРАНЯЕТ список найденных продуктов в Supabase.
    💡 ИСПРАВЛЕНО: Убеждаемся, что для поиска используется 'user_id'.
    """
    try:
        response = supabase.table('users').update({
            'last_search_results': products 
        }).eq('user_id', user_id).execute() # <--- ИСПРАВЛЕНО: .eq('user_id', user_id)
        return response
    except Exception as e:
        logger.error(f"[DB] Ошибка при сохранении результатов для {user_id}: {e}")
        return None


def get_last_products(user_id: int) -> list:
    """
    ИЗВЛЕКАЕТ список найденных продуктов из Supabase.
    💡 ИСПРАВЛЕНО: Убеждаемся, что для поиска используется 'user_id'.
    """
    try:
        response = (supabase.table('users')
                    .select('last_search_results')
                    .eq('user_id', user_id) # <--- ИСПРАВЛЕНО: .eq('user_id', user_id)
                    .single()
                    .execute())

        data = response.data
        if data and data.get('last_search_results'):
            return data['last_search_results']
        
        return []
    except Exception as e:
        logger.warning(f"[DB] Контекст не найден для {user_id}: {e}")
        return []

# 🚀 НОВАЯ/ИСПРАВЛЕННАЯ ФУНКЦИЯ
def clear_last_products(user_id: int) -> None:
    """
    Очищает список последних найденных товаров (контекст RAG) для пользователя.
    💡 КРИТИЧЕСКИ ИСПРАВЛЕНО: Теперь использует 'user_id' и 'last_search_results'.
    """
    try:
        # Используем фактическое имя колонки 'last_search_results'
        # Используем 'user_id' для поиска пользователя, чтобы избежать ошибки 42703 ('column users.id does not exist')
        supabase.table("users").update({"last_search_results": None}).eq("user_id", user_id).execute() # <--- ИСПРАВЛЕНО
        logger.info("Контекст последних продуктов очищен для пользователя %d", user_id)
    except Exception as e:
        logger.error("Ошибка при очистке последних продуктов для %d: %s", user_id, e)
        

# ==============================================================================
# 🚀 НОВЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С ПАРТНЕРАМИ
# ==============================================================================

def assign_partner_by_code(user_id: int, referral_code: str):
    """
    Находит партнера по коду и привязывает его к пользователю.
    """
    try:
        code_clean = referral_code.strip() # Убираем лишние пробелы
        # 1. Ищем партнера по коду
        res = supabase.table("partners").select("id").eq("referral_code", code_clean).maybe_single().execute()
        if res.data:
            partner_id = res.data["id"]
            # 2. Привязываем к пользователю
            supabase.table("users").update({"partner_id": partner_id}).eq("user_id", user_id).execute()
            logger.info(f"Пользователь {user_id} привязан к партнеру {referral_code} (ID: {partner_id})")
            return True
    except Exception as e:
        logger.error(f"Ошибка при привязке партнера: {e}")
    return False

def get_manager_phone_for_user(user_id: int) -> str:
    """
    Возвращает номер телефона менеджера для конкретного пользователя.
    Логика:
    1. Если у юзера есть партнер И подписка партнера активна -> номер партнера.
    2. Иначе -> дефолтный номер из конфига.
    
    💡 ИСПРАВЛЕНО 2026-02-25: Заменён ненадёжный PostgREST JOIN на два отдельных запроса.
    Старый вариант с select("partner_id, partners(...)") ломался при сбросе PostgREST schema cache.
    """
    default_phone = config.DEFAULT_MANAGER_PHONE
    
    try:
        # --- Шаг 1: Получаем partner_id пользователя ---
        user_res = supabase.table("users").select("partner_id").eq("user_id", user_id).single().execute()
        
        if not user_res.data or not user_res.data.get("partner_id"):
            logger.debug(f"[PHONE] У пользователя {user_id} нет привязанного партнера. Отдаём дефолтный номер.")
            return default_phone
        
        partner_id = user_res.data["partner_id"]
        logger.debug(f"[PHONE] Пользователь {user_id} привязан к партнеру ID={partner_id}.")
        
        # --- Шаг 2: Получаем данные партнера отдельным запросом ---
        partner_res = supabase.table("partners").select("phone_number, subscription_end_date").eq("id", partner_id).single().execute()
        
        if not partner_res.data:
            logger.warning(f"[PHONE] Партнер ID={partner_id} не найден в таблице partners! Отдаём дефолтный номер.")
            return default_phone
        
        partner = partner_res.data
        phone = partner.get("phone_number")
        end_date_str = partner.get("subscription_end_date")
        
        logger.debug(f"[PHONE] Партнер ID={partner_id}: phone={phone}, subscription_end={end_date_str}")
        
        # 1. Если даты нет — считаем подписку бессрочной
        if not end_date_str:
            logger.info(f"[PHONE] Партнер ID={partner_id}: подписка бессрочная. Отдаём номер партнера: {phone}")
            return phone or default_phone

        # 2. Если дата есть — парсим её аккуратно
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        except ValueError:
            end_date = datetime.fromisoformat(end_date_str)
        
        # Если дата "наивная" (без таймзоны), принудительно ставим UTC
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        if end_date > datetime.now(timezone.utc):
            logger.info(f"[PHONE] Партнер ID={partner_id}: подписка активна до {end_date_str}. Отдаём номер партнера: {phone}")
            return phone or default_phone
        else:
            logger.info(f"[PHONE] Партнер ID={partner_id}: подписка ИСТЕКЛА {end_date_str}. Отдаём дефолтный номер.")
    
    except Exception as e:
        logger.error(f"[PHONE] Ошибка при получении номера менеджера для {user_id}: {e}", exc_info=True)
    
    return default_phone

# ==============================================================================
# 2. ФУНКЦИИ LLM и УСКОРЕННЫЙ ПОИСК (ОСТАВЛЕНЫ БЕЗ ИЗМЕНЕНИЙ)
# ==============================================================================
def get_product_text_for_embedding(product_data: dict) -> str:
    """
    💡 ИСПРАВЛЕНО: Эта функция должна быть точной копией
    аналогичной функции из embeddings.py для консистентности векторов.
    """
    name = product_data.get('name', '')
    desc = product_data.get('description', '')
    tags = product_data.get('search_tags', '')

    combined_text = (f"Товар: {name}\nТеги для поиска: {tags}\nОписание: {desc}")
    
    # 💡 КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Нормализация к нижнему регистру
    return combined_text.lower()



def embed_text(text: str):
    """Получает эмбеддинг текста через OpenAI."""
    normalized_text = text.lower()  
    try:
        response = openai_client.embeddings.create(
            input = normalized_text,
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"[EMBED] Ошибка генерации эмбеддинга: {e}")
        return None


def search_product_chunks(query: str, top_k: int = 10):
    """
    Ищет релевантные ФРАГМЕНТЫ (chunks) в базе данных.
    Возвращает список словарей, каждый из которых содержит `product_id` и `content`.
    """
    normalized_query = query.lower()
    query_vector = embed_text(normalized_query)
    if not query_vector:
        return []

    response = supabase.rpc(
        "match_chunks",
        {
            "query_embedding": query_vector, 
            "match_count": top_k
        }
    ).execute()

    if not response.data:
        return []

    return response.data

def get_products_by_ids(product_ids: list) -> list:
    """Получает полную информацию о товарах по списку их ID."""
    if not product_ids:
        return []
    
    response = supabase.rpc(
        "get_products_by_ids", # Предполагается, что такая RPC функция создана
        {"p_ids": product_ids}
    ).execute()
    
    return response.data or []

def search_products_by_price_range(price: float, price_range: float = 200.0) -> list:
    """
    Ищет товары в заданном ценовом диапазоне.
    """
    min_price = price - price_range
    max_price = price + price_range
    
    logger.info(f"[DB] Ищу товары в диапазоне цен: {min_price} - {max_price}")
    
    try:
        response = (
            supabase.table("products")
            .select("*")
            .gte("price", min_price)
            .lte("price", max_price)
            .order("price", desc=False) # Сортируем от дешевых к дорогим
            .execute()
        )
        products = response.data or []
        logger.info(f"[DB] Поиск по цене нашел {len(products)} товаров.")
        return products
    except Exception as e:
        logger.error(f"[DB] Ошибка при поиске по диапазону цен: {e}")
        return []

def filter_products_by_category(query: str) -> list:
    """
    Извлекает категорию из запроса и ищет ВСЕ товары в этой категории.
    Используется, когда основной поиск не дал результатов.
    """
    try:
        # Просим LLM извлечь только категорию
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Твоя задача - извлечь из запроса пользователя ОДНО слово, обозначающее категорию товара (например, 'шампунь', 'крем', 'чай', 'бальзам', 'капсулы'). Если категорию извлечь не удается, верни пустую строку."},
                {"role": "user", "content": query}
            ],
            temperature=0
        )
        category = response.choices[0].message.content.strip().lower()

        if not category:
            return []

        logger.info(f"[DB] Извлечена категория для широкого поиска: '{category}'")

        # Ищем все товары, где название или теги содержат эту категорию
        # Используем существующую RPC-функцию для поиска по ключевым словам.
        keyword_products_response = supabase.rpc(
            "keyword_search_products",
            {"search_terms": [category]}
        ).execute()

        products = keyword_products_response.data or []
        logger.info(f"[DB] Широкий поиск нашел {len(products)} товаров в категории '{category}'.")
        return products

    except Exception as e:
        logger.error(f"[DB] Ошибка при широком поиске по категории: {e}")
        return []



def reformulate_query_with_llm(query: str) -> Optional[str]:
    """
    Использует LLM для извлечения ключевых поисковых терминов из сложного запроса.
    "Как принимать женьшень и krill oil" -> "женьшень, масло криля"
    """
    try:
        system_prompt = (
            "Твоя задача — превратить запрос пользователя в простой и чистый поисковый запрос. "
            "**Обязательно исправляй возможные опечатки в словах (например, 'шампун' -> 'шампунь', 'крил' -> 'криль').** "
            "Извлеки только названия товаров, их компоненты или категории. "
            "Также переводи иностранные названия на русский (например, 'krill oil' -> 'масло криля', 'ginseng' -> 'женьшень'). "
            "Убери все лишние слова, такие как 'как принимать', 'сколько стоит', 'есть ли у вас'. "
            "Результат верни в виде строки, где ключевые слова разделены запятой. "
            "Если извлечь ключевые слова не удалось, верни пустую строку."
        )
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            temperature=0
        )
        reformulated_query = response.choices[0].message.content.strip()
        return reformulated_query if reformulated_query else None
    except Exception as e:
        logger.error(f"[DB] Ошибка при переформулировании запроса: {e}")
        return None

def _get_clean_words(query: str) -> list[str]:
    if not query: return []
    """Разбивает запрос на слова и убирает стоп-слова."""
    words = query.lower().replace(',', ' ').replace('.', ' ').split()
    return [w for w in words if w not in STOPWORDS]

def search_products_by_exact_match(query: str) -> list:
    """
    Ищет точное совпадение фразы в названии или тегах.
    Приоритетный поиск для фраз типа 'жидкое иглоукалывание'.
    """
    try:
        # Очищаем запрос от лишних символов, но оставляем пробелы
        # 💡 УЛУЧШЕНИЕ: Убираем стоп-слова из начала фразы (например, "есть жидкое..." -> "жидкое...")
        words = query.lower().split()
        while words and words[0] in STOPWORDS:
            words.pop(0)
        
        clean_query = " ".join(words).strip()

        if not clean_query or len(clean_query) < 3:
            return []
            
        # 💡 ИЗМЕНЕНИЕ: Ищем фразу везде, включая ОПИСАНИЕ (description).
        # Это позволит находить "L-теанин", даже если он есть только в тексте состава.
        response = supabase.table("products").select("id, name, price, description, search_tags") \
            .or_(f"name.ilike.%{clean_query}%,search_tags.ilike.%{clean_query}%,description.ilike.%{clean_query}%") \
            .limit(10) \
            .execute()
        
        data = response.data or []
        if data:
            logger.info(f"[DB] ✅ Точный поиск нашел {len(data)} товаров по запросу '{clean_query}'")
        return data
    except Exception as e:
        logger.error(f"[DB] Ошибка при точном поиске: {e}")
        return []

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ПОИСКА (RETRIEVERS) ---

def _fetch_keyword_candidates(user_query: str) -> set:
    """Ищет ID товаров по ключевым словам (леммы и исходные формы)."""
    ids = set()
    
    # По исходным словам
    clean_words = _get_clean_words(user_query)
    if clean_words:
        try:
            res_orig = supabase.rpc("keyword_search_products", {"search_terms": clean_words}).execute()
            if res_orig.data:
                ids.update(p['id'] for p in res_orig.data)
        except Exception as e:
            logger.warning(f"[DB] Ошибка поиска по словам: {e}")
            
    return ids

# ⚙️ ГЛАВНАЯ ФУНКЦИЯ ПОИСКА (Refactored)
async def search_products(user_query: str):
    """
    Модульный гибридный поиск:
    1. Retrieve: Сбор кандидатов из разных источников (Exact, Vector, Keywords).
    2. Rank: (В будущем) Переранжирование. Сейчас - объединение.
    """
    logger.info(f"🔎 Запуск поиска товаров по запросу: '{user_query}'")
    
    loop = asyncio.get_running_loop()
    
    # --- ЭТАП 1: СБОР КАНДИДАТОВ (RETRIEVAL) ---
    
    # 1. Точное совпадение (High Precision)
    exact_products = await loop.run_in_executor(None, search_products_by_exact_match, user_query)
    exact_ids = {p['id'] for p in exact_products}
    
    # 2. Векторный поиск по чанкам (High Recall)
    raw_chunks = await loop.run_in_executor(None, search_product_chunks, user_query, 10)
    
    # 💡 ФИЛЬТРАЦИЯ: Отсекаем мусор с низким сходством (порог 0.65)
    chunks = [c for c in raw_chunks if c.get('similarity', 0) > 0.65]
    
    chunk_ids = {chunk['product_id'] for chunk in chunks}
    
    # 3. Ключевые слова (Backup)
    # Запускаем только если точный поиск дал мало результатов, чтобы не шуметь
    keyword_ids = set()
    if len(exact_ids) < 2:
        keyword_ids = await loop.run_in_executor(None, _fetch_keyword_candidates, user_query)

    # --- ЭТАП 2: ОБЪЕДИНЕНИЕ И РАНЖИРОВАНИЕ (RANKING) ---
    
    # Здесь можно подключить ReRanker (например, Cohere Rerank или FlashRank).
    # Пока используем эвристику: Точные > Векторные > Ключевые.
    
    all_ids = set()
    all_ids.update(exact_ids)
    all_ids.update(chunk_ids)
    all_ids.update(keyword_ids)
    
    if not all_ids:
        return [], [] # Ничего не найдено

    # Превращаем set в список для запроса к БД
    final_ids_list = list(all_ids)
    
    # Получаем полные данные товаров
    products_data = await loop.run_in_executor(None, get_products_by_ids, final_ids_list)
    
    # 💡 ПРОСТАЯ СОРТИРОВКА (Вместо ReRanker пока что):
    # Поднимаем наверх те, что нашлись точным поиском
    def sort_key(p):
        if p['id'] in exact_ids: return 0 # Самый высокий приоритет
        if p['id'] in chunk_ids: return 1
        return 2
        
    sorted_products = sorted(products_data, key=sort_key)
    
    logger.info(f"[DB] 🏁 Найдено {len(sorted_products)} товаров. Топ-3 ID: {[p['id'] for p in sorted_products[:3]]}")

    return sorted_products, chunks


def check_user_access(user_id: int) -> tuple[bool, str, Optional[str]]:
    """
    Проверяет доступ пользователя к боту.
    Возвращает: (has_access, role, partner_phone)
    """
    try:
        # 1. Проверяем, является ли пользователь администратором
        if user_id in config.ADMIN_IDS:
            return True, "admin", None

        # 2. Проверяем, является ли пользователь партнером
        p_res = supabase.table("partners").select("id, subscription_end_date").eq("telegram_user_id", user_id).execute()
        if p_res.data and len(p_res.data) > 0:
            partner = p_res.data[0]
            end_date_str = partner.get("subscription_end_date")
            
            # Нет даты = бессрочная подписка
            if not end_date_str:
                return True, "partner", None
                
            # Парсим дату
            try:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except ValueError:
                end_date = datetime.fromisoformat(end_date_str)
                
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
                
            if end_date > datetime.now(timezone.utc):
                return True, "partner", None
            else:
                return False, "partner", None

        # 3. Если не партнер — это обычный клиент
        # Ищем его привязанного партнера
        u_res = supabase.table("users").select("partner_id").eq("user_id", user_id).execute()
        if not u_res.data or len(u_res.data) == 0 or u_res.data[0].get("partner_id") is None:
            # Нет партнера (привязываем к Надежде в фоне)
            p_def = supabase.table("partners").select("id, phone_number").eq("referral_code", config.DEFAULT_PARTNER_CODE).execute()
            if p_def.data and len(p_def.data) > 0:
                def_id = p_def.data[0]["id"]
                supabase.table("users").update({"partner_id": def_id}).eq("user_id", user_id).execute()
                return True, "client", p_def.data[0].get("phone_number")
            return True, "client", None

        partner_id = u_res.data[0]["partner_id"]
        
        # Получаем данные этого партнера
        p_data_res = supabase.table("partners").select("phone_number, subscription_end_date").eq("id", partner_id).execute()
        if not p_data_res.data or len(p_data_res.data) == 0:
            return True, "client", None
            
        partner = p_data_res.data[0]
        phone = partner.get("phone_number")
        end_date_str = partner.get("subscription_end_date")
        
        if not end_date_str:
            return True, "client", phone
            
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        except ValueError:
            end_date = datetime.fromisoformat(end_date_str)
            
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
            
        if end_date > datetime.now(timezone.utc):
            return True, "client", phone
        else:
            return False, "client", phone

    except Exception as e:
        logger.error(f"[DB] Ошибка при проверке доступа для {user_id}: {e}", exc_info=True)
        return True, "client", None