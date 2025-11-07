# embeddings.py

# ...existing code...
import logging
import time
from typing import List, Optional

from openai import OpenAI
import config
from supabase import create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ... (проверки config остаются прежними)

client = OpenAI(api_key=config.OPENAI_API_KEY)
supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

EMBED_MODEL = "text-embedding-3-small" # 1536 dims

# =================================================================
# 💡 ДОБАВЛЕНА: Функция для сбора текста (Используется в backfill)
# =================================================================
def get_product_text_for_embedding(product_data: dict) -> str:
    """Объединяет поля name, description, search_tags и приводит к нижнему регистру."""
    name = product_data.get('name', '')
    desc = product_data.get('description', '')
    tags = product_data.get('search_tags', '')

    combined_text = (
        f"Товар: {name}\n"
        f"Теги для поиска: {tags}\n"
        f"Описание: {desc}"
    )
    # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Нормализация к нижнему регистру
    return combined_text.lower()


def generate_search_tags(description: str) -> str:
    """
    Использует LLM для генерации плотных, релевантных ключевых слов и терминов.
    """
    if not client or not description or len(description) < 20:
        return ""

    prompt = (
        "Ты — эксперт по продуктам Greenleaf. Твоя задача — извлечь из описания продукта "
        "наиболее релевантные ключевые слова и фразы, которые пользователи могут использовать "
        "для поиска этого продукта. Сгенерируй список из 5-10 плотных, релевантных тегов, "
        "разделенных запятыми. Избегай общих слов типа 'продукт', 'средство'. "
        "Примеры: 'шампунь для волос', 'увлажняющий крем для лица', 'витамины для иммунитета'."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt},
                # Отправляем LLM описание как есть, но на выходе нормализуем
                {"role": "user", "content": description}
            ],
            temperature=0.0
        )
        tags = response.choices[0].message.content.strip()
        return tags.lower() # 💡 ИЗМЕНЕНИЕ: Теги всегда сохраняем в нижнем регистре
    except Exception as e:
        logger.error(f"⚠️ Ошибка LLM при генерации тегов: {e}")
        return ""

def _extract_embedding(resp) -> Optional[List[float]]:
    # ... (функция _extract_embedding остается прежней)
    try:
        if hasattr(resp, "data") and resp.data:
            emb = resp.data[0].embedding
        else:
            emb = resp.get("data", [None])[0].get("embedding")
    except Exception:
        emb = None
    if emb is None:
        return None
    return list(emb)

def embed_text(text: str) -> Optional[List[float]]:
    if not text:
        return None
    
    # 💡 КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Нормализуем входящий текст ПЕРЕД векторизацией
    normalized_text = text.lower()
    
    try:
        resp = client.embeddings.create(model=EMBED_MODEL, input=normalized_text)
        emb = _extract_embedding(resp)
        if not emb:
            logger.warning("Пустой embedding для текста: %r", normalized_text[:200])
        return emb
    except Exception as e:
        logger.exception("Ошибка при создании эмбеддинга: %s", e)
        return None

# 💡 ИЗМЕНЕНИЕ: Добавлен аргумент force_regenerate
def backfill_product_embeddings(batch_size: int = 500, pause_between: float = 0.1, force_regenerate: bool = False):
    
    # ====================================================
    # 1. ТАГГИРОВАНИЕ: Ищем товары без search_tags
    # Теги теперь всегда сохраняются в нижнем регистре.
    # ====================================================
    # ... (Код Шага 1 остается прежним, кроме того, что generate_search_tags возвращает lower())
    logger.info("--- ШАГ 1: ГЕНЕРАЦИЯ ТЕГОВ (search_tags) ---")
    
    tag_res = (
        supabase.table("products")
        .select("id,name,description")
        .not_.is_("description", None)
        .is_("search_tags", None)
        .limit(batch_size)
        .execute()
    )
    items_to_tag = tag_res.data
    logger.info("Найдено %d продуктов, требующих тегирования", len(items_to_tag))
    
    # ... (Цикл по items_to_tag остается прежним: tags = generate_search_tags(description) теперь возвращает lower())
    for p in items_to_tag:
        pid = p.get("id")
        desc = p.get("description", "")
        
        # 1. Генерация тегов
        tags = generate_search_tags(desc) 
        
        if not tags:
            logger.warning("Пропускаем продукт %s — не удалось сгенерировать теги.", pid)
            time.sleep(pause_between)
            continue
            
        # 2. Обновление в Supabase
        try:
            upd = supabase.table("products").update({"search_tags": tags}).eq("id", pid).execute()
            logger.info("Успешно обновлен search_tags для ID=%s: %s", pid, tags)
        except Exception as e:
            logger.error("Ошибка обновления тега для ID=%s: %s", pid, e)# ... (код обновления)
        time.sleep(pause_between)
    
    # ====================================================
    # 2. ЭМБЕДДИНГ: Ищем товары без embedding ИЛИ все товары (если --force)
    # ====================================================
    logger.info("--- ШАГ 2: РАСЧЕТ ЭМБЕДДИНГОВ (embedding) ---")
    
    query = supabase.table("products").select("id,name,description,price,images,search_tags,pv,embedding")
    
    if not force_regenerate:
        # Обычный Backfill: ищем только те, у кого нет вектора
        query = query.is_("embedding", None)
        logger.info("Запущен режим BACKFILL: ищем только товары без эмбеддинга.")
    else: 
        # Полная регенерация: выбираем все
        logger.warning("Запущен режим ПОЛНОЙ РЕГЕНЕРАЦИИ (force=True). Будут обновлены ВСЕ эмбеддинги.")
    
    # ЗАПРОС
    res = query.limit(batch_size).execute()
    items_to_embed = res.data 
    logger.info("Найдено %d продуктов, требующих эмбеддинга", len(items_to_embed))
    
    for p in items_to_embed:
        pid = p.get("id")
        tags = p.get("search_tags", "")
        
        if not tags and not p.get("description"):
            logger.warning("Пропускаем продукт %s — нет ни описания, ни тегов", pid)
            continue
        
        try:
            # 💡 КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Используем новую функцию для подготовки текста (с .lower())
            combined_text = get_product_text_for_embedding(p)
            
            # embed_text также делает .lower() (двойная защита)
            vec = embed_text(combined_text) 
            
            if not vec:
                logger.warning("Пропускаем продукт %s — пустой вектор", pid)
                continue
            
            logger.info("ID=%s, vector length=%d", pid, len(vec))
            upd = supabase.table("products").update({"embedding": vec}).eq("id", pid).execute()
            # ... (обработка ошибок)
            
        except Exception as e:
            logger.exception("Ошибка для продукта %s: %s", pid, e)
        finally:
            time.sleep(pause_between)
            
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=100, help="Размер батча для backfill")
    parser.add_argument("--pause", type=float, default=0.1, help="Пауза между запросами (с)")
    # 💡 НОВЫЙ АРГУМЕНТ
    parser.add_argument("--force", action='store_true', help="Принудительно перегенерировать ВСЕ эмбеддинги.") 
    args = parser.parse_args()
    
    backfill_product_embeddings(
        batch_size=args.batch, 
        pause_between=args.pause,
        force_regenerate=args.force # Передаем аргумент
    )