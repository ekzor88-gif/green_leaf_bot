from supabase import create_client, ClientOptions
from openai import OpenAI
import config
import json 
import logging
import asyncio 
from typing import Optional
from datetime import datetime, timezone # 💡 Для проверки даты подписки
import pymorphy3 # 💡 НОВАЯ БИБЛИОТЕКА

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

print("⏳ [DB] Подключение к Supabase...")
# 💡 Инициализация синхронных клиентов с увеличенным таймаутом
# Это нужно, чтобы "холодный старт" базы на бесплатном тарифе не вызывал ошибку.
options = ClientOptions(postgrest_client_timeout=30)
supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY, options=options)
print("✅ [DB] Supabase клиент создан.")

print("⏳ [DB] Подключение к OpenAI...")
openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

# 💡 ОПТИМИЗАЦИЯ ПАМЯТИ (Lazy Loading)
# Мы не создаем анализатор сразу, чтобы бот не падал при старте из-за нехватки RAM.
_morph = None

def get_morph():
    """Ленивая загрузка морфологического анализатора."""
    global _morph
    if _morph is None:
        logger.info("⏳ [DB] Инициализация pymorphy3 (первый запуск)...")
        _morph = pymorphy3.MorphAnalyzer()
        logger.info("✅ [DB] Анализатор загружен.")
    return _morph

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
    💡 Коррекция: Здесь используется 'user_id', что корректно.
    """
    try:
        return supabase.table("users").upsert({
            "user_id": user_id,
            "first_name": first_name,
            "last_name": last_name,
            "username": username,
        }).execute()
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
    """
    default_phone = config.DEFAULT_MANAGER_PHONE
    
    try:
        # Запрашиваем данные пользователя вместе с данными партнера
        # Синтаксис select: "partner_id, partners(...)" позволяет сделать JOIN
        res = supabase.table("users").select("partner_id, partners(phone_number, subscription_end_date)").eq("user_id", user_id).single().execute()
        
        if res.data and res.data.get("partners"):
            partner = res.data["partners"]
            end_date_str = partner.get("subscription_end_date")
            
            if end_date_str:
                # Парсим дату (Supabase возвращает ISO формат)
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                # Проверяем, не истекла ли подписка
                if end_date > datetime.now(timezone.utc):
                    return partner.get("phone_number")
    
    except Exception as e:
        logger.error(f"Ошибка при получении номера менеджера для {user_id}: {e}")
    
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

def _get_lemmas(query: str) -> list[str]:
    """
    Возвращает список лемм (начальных форм) слов из запроса.
    """
    words = _get_clean_words(query)
    lemmas = set()
    for word in words:
        # 💡 ИСПОЛЬЗУЕМ ФУНКЦИЮ get_morph() ВМЕСТО ГЛОБАЛЬНОЙ ПЕРЕМЕННОЙ
        normal_form = get_morph().parse(word)[0].normal_form
        lemmas.add(normal_form)
    return list(lemmas)

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
    
    # 1. По леммам
    lemmas = _get_lemmas(user_query)
    if lemmas:
        try:
            res_lemma = supabase.rpc("keyword_search_products", {"search_terms": lemmas}).execute()
            if res_lemma.data:
                ids.update(p['id'] for p in res_lemma.data)
        except Exception as e:
            logger.warning(f"[DB] Ошибка поиска по леммам: {e}")

    # 2. По исходным словам
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
    chunks = await loop.run_in_executor(None, search_product_chunks, user_query, 10)
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