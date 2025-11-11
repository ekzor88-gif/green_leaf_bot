from supabase import create_client
from openai import OpenAI
import config
import json 
import logging
import asyncio 
import pymorphy3 # 💡 НОВАЯ БИБЛИОТЕКА

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# 💡 Инициализация синхронных клиентов
supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
morph = pymorphy3.MorphAnalyzer() # 💡 Инициализация анализатора


# ==============================================================================
# 1. ФУНКЦИИ РАБОТЫ С БАЗОЙ ДАННЫХ (С КОРРЕКЦИЕЙ ID И КОНТЕКСТА)
# ==============================================================================

def upsert_user(user_id: int, first_name: str, last_name: str, username: str):
    """
    Обновляет или создает пользователя. 
    💡 Коррекция: Здесь используется 'user_id', что корректно.
    """
    return supabase.table("users").upsert({
        "user_id": user_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
    }).execute()


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

# 🚀 ПОЛНОСТЬЮ ПЕРЕРАБОТАННАЯ ФУНКЦИЯ
def _lemmatize_and_clean_query(query: str) -> list[str]:
    """
    Приводит слова в запросе к начальной форме (лемматизирует) и удаляет стоп-слова.
    "шампунь с имбирем" -> ["шампунь", "имбирь"]
    """
    stopwords = {
        "с", "в", "на", "за", "из", "для", "от", "по", "у", "о", "без", "и", "а", "но",
        "быть", "весь", "этот", "который", "мой", "наш", "ваш"
    }
    words = query.lower().replace(',', ' ').replace('.', ' ').split()
    lemmatized_words = []
    for word in words:
        if word not in stopwords:
            lemmatized_words.append(morph.parse(word)[0].normal_form)
    return lemmatized_words

# ⚙️ Общая функция — Одинарный поиск (Быстро и Точно)
def search_products(user_query: str):
    """
    Основная функция ГИБРИДНОГО поиска. Комбинирует семантический поиск по фрагментам
    и поиск по ключевым словам в названии и тегах.
    """
    # --- Шаг 0: Очистка запроса для ключевого поиска ---
    cleaned_query_words = _lemmatize_and_clean_query(user_query)

    # --- Шаг 1: Семантический поиск по фрагментам (как и раньше) ---
    chunks = search_product_chunks(user_query)
    
    # --- Шаг 2: Поиск по ключевым словам ---
    keyword_products_response = supabase.rpc(
        "keyword_search_products",
        {"search_terms": cleaned_query_words} # <-- ИСПОЛЬЗУЕМ ОЧИЩЕННЫЙ ЗАПРОС
    ).execute()
    keyword_products = keyword_products_response.data or []

    # --- Шаг 3: Объединение результатов ---
    # Собираем ID из семантического поиска
    semantic_product_ids = {chunk['product_id'] for chunk in chunks}
    # Собираем ID из поиска по ключевым словам
    keyword_product_ids = {p['id'] for p in keyword_products}
    # Объединяем уникальные ID
    all_unique_ids = sorted(list(semantic_product_ids.union(keyword_product_ids)))

    if not all_unique_ids:
        return [], [] # Ничего не найдено

    # --- Шаг 4: Получение полной информации о товарах ---
    final_products = get_products_by_ids(all_unique_ids)

    return final_products, chunks