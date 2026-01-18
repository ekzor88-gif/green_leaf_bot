import argparse
import logging

import db as db_utils

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_and_embed_chunks(product_id: int, product_name: str, description: str, tags: str):
    """
    Создает и загружает чанки для одного товара: название, описание и каждый тег отдельно.
    Эта функция скопирована из update_catalog.py для консистентности.
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
        logger.info(f"✅ Загружено {len(final_chunks)} новых чанков для товара ID {product_id}.")

def re_embed_product_by_id(product_id: int):
    """
    Полностью пересоздает поисковые чанки и эмбеддинги для одного товара по его ID.
    """
    logger.info(f"🚀 Начинаю пересоздание эмбеддингов для товара с ID={product_id}...")

    try:
        # --- Шаг 1: Найти товар в базе ---
        product_res = db_utils.supabase.table("products").select("name, description, search_tags").eq("id", product_id).maybe_single().execute()
        if not product_res.data:
            logger.error(f"❌ Товар с ID={product_id} не найден. Процесс отменен.")
            return

        product = product_res.data
        logger.info(f"Найден товар для обновления: '{product.get('name')}'")

        # --- Шаг 2: Удалить старые чанки ---
        logger.info(f"🗑️ Удаляю старые поисковые чанки для товара ID={product_id}...")
        db_utils.supabase.table("catalog_chunks").delete().eq("product_id", product_id).execute()
        logger.info("Старые чанки успешно удалены.")

        # --- Шаг 3: Создать и загрузить новые чанки и эмбеддинги ---
        create_and_embed_chunks(product_id, product.get('name'), product.get('description'), product.get('search_tags'))

        logger.info(f"🎉 Успешно завершено пересоздание эмбеддингов для товара ID={product_id}.")

    except Exception as e:
        logger.error(f"❌ Произошла критическая ошибка во время процесса для товара ID={product_id}: {e}", exc_info=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Пересоздать поисковые чанки и эмбеддинги для одного товара по его ID.")
    parser.add_argument("--id", type=int, required=True, help="ID товара в таблице 'products'.")
    args = parser.parse_args()
    
    re_embed_product_by_id(args.id)