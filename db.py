from supabase import create_client
from openai import OpenAI
import config
import json 
import logging
import asyncio 

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# 💡 Инициализация синхронных клиентов
supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
openai_client = OpenAI(api_key=config.OPENAI_API_KEY)


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
    name = product_data.get('name', '')
    desc = product_data.get('description', '')
    tags = product_data.get('search_tags', '')
    
    combined_text = f"Название: {name}. Описание: {desc}. Ключевые слова: {tags}."
    
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


# 🚀 Фаза 1 — Ускоренный векторный поиск (ОСТАВЛЯЕМ ТОЛЬКО ЕГО)
def search_products_phase1(query: str, top_k: int = 15, min_sim: float = 0.45):
    
    # 💡 КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Нормализация запроса
    normalized_query = query.lower() 
    
    # Передаем нормализованный запрос в вашу функцию embed_text
    query_vector = embed_text(normalized_query)
    if not query_vector:
        return []

    # 💡 ВАЖНО: Добавлено поле search_tags для использования в RAG-ответе
    response = supabase.rpc(
        "match_products",
        {"query_embedding": query_vector, "match_count": top_k}
    ).execute()

    results = [
        {
            "id": item["id"],
            "name": item["name"],
            "description": item.get("description", ""),
            "price": item.get("price"),
            "image": item.get("images"),
            "search_tags": item.get("search_tags", ""), # 💡 НОВОЕ: Извлекаем теги
            "similarity": item["similarity"],
        }
        for item in response.data
        if item["similarity"] >= min_sim
    ]

    # Рекурсивный откат
    if not results and min_sim > 0.15:
        logger.debug(f"[SEARCH] Снижаем порог до {min_sim - 0.05}")
        return search_products_phase1(query, top_k, min_sim - 0.05)

    return results


# ⚙️ Общая функция — Одинарный поиск (Быстро и Точно)
def search_products(user_query: str):
    """
    Основная функция поиска. Возвращает список релевантных товаров 
    после векторного поиска.
    """
    return search_products_phase1(user_query)