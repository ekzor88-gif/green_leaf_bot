import os
import re # Импортируем модуль re для работы с регулярными выражениями
import uuid
import time
import logging
from typing import List, Tuple, Dict
from docx import Document # Используем только python-docx
from supabase import create_client, Client
from dotenv import load_dotenv

# 💡 ИЗМЕНЕНИЕ: Импортируем функции для генерации тегов и эмбеддингов
import config
import db as db_utils
from embeddings import generate_search_tags

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOCX_FILE = "catalog.docx"
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "products")

def guess_ext_and_mime(content_type: str, partname: str) -> Tuple[str, str]:
    ext = ""
    mime = content_type or "application/octet-stream"
    if partname:
        _, ext_candidate = os.path.splitext(str(partname))
        if ext_candidate:
            ext = ext_candidate.lower().lstrip(".")
    if not ext:
        mime_map = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}
        ext = mime_map.get(mime, "jpg")
    return ext, mime

def create_and_embed_chunks(product_id: int, product_name: str, description: str, tags: str):
    """
    Создает и загружает чанки для одного товара: название, описание и каждый тег отдельно.
    """
    chunks_to_insert = []

    # 1. Чанк для названия
    chunks_to_insert.append({"product_id": product_id, "content": product_name})

    # 2. Чанк для описания
    if description:
        chunks_to_insert.append({"product_id": product_id, "content": description})

    # 3. Отдельные чанки для каждого тега
    if tags:
        tag_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
        for tag in tag_list:
            chunks_to_insert.append({"product_id": product_id, "content": tag})

    # 4. Генерируем эмбеддинги и готовим к загрузке
    final_chunks = []
    for chunk in chunks_to_insert:
        embedding = db_utils.embed_text(chunk['content'])
        if embedding:
            chunk['embedding'] = embedding
            final_chunks.append(chunk)
    
    # 5. Загружаем все чанки для товара одним запросом
    if final_chunks:
        db_utils.supabase.table("catalog_chunks").insert(final_chunks).execute()
        logger.info(f"✅ Загружено {len(final_chunks)} чанков для товара ID {product_id}.")

def process_and_embed_catalog(docx_path: str):
    """
    Основная функция: парсит таблицу из DOCX, извлекает товары, создает эмбеддинги и загружает в Supabase.
    """
    if not os.path.exists(docx_path):
        logger.error(f"Файл каталога не найден: {docx_path}")
        return
        
    logger.info(f"📄 Начинаю обработку файла: {docx_path}")
    
    # 1. Очищаем старые данные
    logger.info("🗑️ Очищаю старые фрагменты (chunks)...")
    # 💡 ИЗМЕНЕНИЕ: Удаляем записи батчами, чтобы избежать таймаута
    batch_size = 1000  # Размер батча (можно настроить)
    while True:
        # 1. Получаем ID следующего батча
        res = db_utils.supabase.table("catalog_chunks").select("id").limit(batch_size).execute()
        
        # 2. Если данных нет, значит таблица пуста, выходим
        if not res.data:
            logger.info("✅ Все старые фрагменты удалены.")
            break
            
        # 3. Собираем ID для удаления и удаляем батч
        ids_to_delete = [item["id"] for item in res.data]
        db_utils.supabase.table("catalog_chunks").delete().in_("id", ids_to_delete).execute()
        logger.info(f"🗑️ Удалено {len(ids_to_delete)} фрагментов (chunks)...")
        
        # 4. Если удалили меньше, чем размер батча, это был последний батч.
        if len(ids_to_delete) < batch_size:
            logger.info("✅ Все старые фрагменты удалены.")
            break
    
    # 2. Открываем документ и итерируем по таблицам
    doc = Document(docx_path)
    total_products_processed = 0

    for table_idx, table in enumerate(doc.tables):
        logger.info(f"--- Обработка таблицы #{table_idx+1} ---")
        
        for row_idx, row in enumerate(table.rows):
            # Пропускаем заголовок таблицы
            if row_idx == 0:
                continue

            try:
                # --- 3. Извлечение данных из ячеек ---
                # Ячейка 0: Фото
                # Ячейка 1: Название
                # Ячейка 2: Описание
                # Ячейка 3: Цена (предполагаем, что цена находится здесь)
                
                name_cell = row.cells[1]
                description_cell = row.cells[2]
                price_cell = row.cells[3] # Предполагаем, что цена находится в 4-й ячейке
                
                product_name = name_cell.text.strip()
                description = description_cell.text.strip()
                raw_price = price_cell.text.strip()
                product_price = None
                try:
                    # Удаляем все нечисловые символы, кроме точки/запятой, и пытаемся конвертировать в число
                    cleaned_price = re.sub(r'[^\d.,]+', '', raw_price).replace(',', '.')
                    product_price = float(cleaned_price)
                except ValueError:
                    logger.warning(f"Не удалось распарсить цену '{raw_price}' для товара '{product_name}'.")

                if not product_name or not description:
                    logger.warning(f"Пропускаю строку #{row_idx+1}: нет названия или описания.")
                    continue

                logger.info(f"Найдена запись: '{product_name}'")

                # --- 4. Извлечение и загрузка изображения ---
                image_url = None
                image_cell = row.cells[0]
                blips = image_cell._element.xpath(".//a:blip")
                if blips:
                    rid = blips[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                    if rid:
                        image_part = doc.part.related_parts[rid]
                        ext, mime = guess_ext_and_mime(image_part.content_type, str(image_part.partname))
                        
                        # Загрузка в Supabase Storage
                        try:
                            path = f"{uuid.uuid4()}_product.{ext}"
                            db_utils.supabase.storage.from_(SUPABASE_BUCKET).upload(
                                path, image_part.blob, file_options={"content-type": mime, "upsert": "true"}
                            )
                            image_url = f"{config.SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"
                            logger.info(f"🖼️  Изображение для '{product_name}' загружено: {image_url}")
                        except Exception as img_e:
                            logger.error(f"❌ Не удалось загрузить изображение для '{product_name}': {img_e}")
                            image_url = None # Явно сбрасываем URL в случае ошибки

                # --- 5. Умная генерация тегов и обновление товара ---
                tags = None
                # Ищем товар в базе по имени, чтобы проверить, нужно ли обновлять теги
                existing_product_res = db_utils.supabase.table("products").select("description, search_tags").eq("name", product_name).maybe_single().execute()
                # 💡 ИСПРАВЛЕНИЕ: Добавляем проверку, что ответ от API не пустой (None)
                # Это защищает от ошибки 'NoneType' object has no attribute 'data' при HTTP-ошибках (например, 406 Not Acceptable)
                existing_product = existing_product_res.data if existing_product_res else None
                
                # Генерируем теги только если товара нет, у него нет тегов, или его описание изменилось.
                if not existing_product or not existing_product.get("search_tags") or existing_product.get("description") != description:

                    logger.info(f"Требуется генерация тегов для '{product_name}'. Запускаю LLM...")
                    tags = generate_search_tags(description)
                    if tags:
                        logger.info(f"🏷️  Сгенерированы теги: {tags}")
                    else:
                        logger.warning(f"Не удалось сгенерировать теги для '{product_name}'.")
                else:
                    logger.info(f"Теги для '{product_name}' уже существуют и актуальны. Пропускаю генерацию.")
                    tags = existing_product["search_tags"] # Используем существующие теги

                product_data = {
                    "name": product_name,
                    "description": description,
                    "price": product_price, # Добавляем извлеченную цену
                    "images": [image_url] if image_url else None,
                    "search_tags": tags,
                }
                
                # Используем upsert для атомарного создания/обновления
                response = db_utils.supabase.table("products").upsert(product_data, on_conflict="name").execute()
                product_id = response.data[0]['id']
                logger.info(f"✅ Товар '{product_name}' (ID: {product_id}) сохранен.")

                # --- 6. 💡 НОВЫЙ ШАГ: Создание и загрузка всех чанков ---
                # Создаем чанки, только если есть теги (это наш главный поисковый инструмент)
                if tags:
                    create_and_embed_chunks(product_id, product_name, description, tags)
                
                total_products_processed += 1

            except Exception as e:
                logger.error(f"❌ Ошибка обработки строки #{row_idx+1}: {e}", exc_info=True)
            finally:
                time.sleep(0.1) # Небольшая пауза, чтобы не перегружать API

    logger.info(f"🎉 Готово! Всего обработано и загружено {total_products_processed} товаров.")

if __name__ == "__main__":
    process_and_embed_catalog(DOCX_FILE)
