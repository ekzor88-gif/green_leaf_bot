import argparse
import logging
import db as db_utils

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def delete_product_by_id(product_id: int):
    """
    Полностью удаляет товар и все связанные с ним поисковые чанки из базы данных.
    """
    logger.info(f"🚀 Начинаю полное удаление товара с ID={product_id}...")

    try:
        # --- Шаг 0: Проверяем, существует ли товар (для логов) ---
        product_res = db_utils.supabase.table("products").select("name").eq("id", product_id).maybe_single().execute()
        if not product_res.data:
            logger.error(f"❌ Товар с ID={product_id} не найден. Удаление отменено.")
            return

        product_name = product_res.data.get("name")
        logger.info(f"Найден товар для удаления: '{product_name}'")

        # --- Шаг 1: Удаляем все поисковые чанки, связанные с этим товаром ---
        logger.info(f"🗑️ Удаляю поисковые чанки для товара ID={product_id}...")
        delete_chunks_res = db_utils.supabase.table("catalog_chunks").delete().eq("product_id", product_id).execute()
        # Supabase v2 delete не возвращает count, просто проверяем на ошибку
        logger.info("Чанки успешно удалены.")

        # --- Шаг 2: Удаляем основную запись о товаре ---
        logger.info(f"🗑️ Удаляю основную запись о товаре ID={product_id}...")
        delete_product_res = db_utils.supabase.table("products").delete().eq("id", product_id).execute()
        logger.info("Основная запись о товаре успешно удалена.")

        logger.info(f"🎉 Успешно завершено полное удаление товара ID={product_id} ('{product_name}').")

    except Exception as e:
        logger.error(f"❌ Произошла критическая ошибка во время удаления товара ID={product_id}: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Полностью удалить товар и все его поисковые данные по ID.")
    parser.add_argument(
        "--id",
        type=int,
        required=True,
        help="ID товара в таблице 'products', который нужно полностью удалить."
    )
    
    args = parser.parse_args()
    
    # Запрос подтверждения для безопасности
    confirm = input(f"Вы уверены, что хотите НАВСЕГДА удалить товар с ID={args.id} и все его данные? (yes/no): ")
    if confirm.lower() == 'yes':
        delete_product_by_id(args.id)
    else:
        logger.info("Удаление отменено пользователем.")