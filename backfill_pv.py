import os
import re
import logging
import time
from docx import Document

import db as db_utils

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DOCX_FILE = "catalog.docx"

def backfill_missing_pv(docx_path: str):
    """
    Служебный скрипт для однократного заполнения отсутствующих значений 'pv' в базе данных.
    Он сравнивает данные из DOCX-каталога с базой и обновляет только те товары,
    где 'pv' в базе пусто, а в каталоге — есть.
    """
    if not os.path.exists(docx_path):
        logger.error(f"❌ Файл каталога не найден по пути: {docx_path}")
        return

    logger.info(f"🚀 Начинаю процесс заполнения недостающих PV из файла '{docx_path}'...")

    doc = Document(docx_path)
    updated_count = 0
    processed_count = 0

    for table_idx, table in enumerate(doc.tables):
        logger.info(f"--- Обработка таблицы #{table_idx+1} ---")

        for row_idx, row in enumerate(table.rows):
            if row_idx == 0:  # Пропускаем заголовок
                continue

            try:
                # Извлекаем название (ячейка 1) и PV (ячейка 4)
                product_name = row.cells[1].text.strip()
                raw_pv = row.cells[4].text.strip()

                if not product_name:
                    continue

                processed_count += 1
                catalog_pv = None
                # Парсим PV из каталога
                if raw_pv:
                    try:
                        cleaned_pv = re.sub(r'[^\d.,]+', '', raw_pv).replace(',', '.')
                        if cleaned_pv:
                            catalog_pv = float(cleaned_pv)
                    except (ValueError, IndexError):
                        logger.warning(f"Не удалось распознать PV '{raw_pv}' для товара '{product_name}'.")

                # Если в каталоге нет PV, то и обновлять нечего
                if not catalog_pv or catalog_pv <= 0:
                    continue

                # 💡 ИСПРАВЛЕНИЕ: Используем .limit(1) вместо .maybe_single()
                # Это предотвращает ошибку "406 Not Acceptable", если в базе есть дубликаты по имени.
                # Мы просто возьмем первую найденную запись для обновления.
                res = db_utils.supabase.table("products").select("id, pv").eq("name", product_name).limit(1).execute()

                # 💡 ИСПРАВЛЕНИЕ: Добавляем проверку, что ответ от API не пустой (None).
                # Это защищает от ошибки 'NoneType' object has no attribute 'data' при HTTP-ошибках.
                if res and res.data:
                    # Так как мы использовали limit(1), мы берем первый элемент из списка
                    product_in_db = res.data[0]
                    db_pv = product_in_db.get("pv")
                    # Обновляем, только если в базе PV отсутствует (None или 0)
                    if not db_pv or db_pv == 0:
                        product_id = product_in_db.get("id")
                        logger.info(f"🔄 Найден товар для обновления: '{product_name}' (ID: {product_id}). В базе PV: {db_pv}, в каталоге: {catalog_pv}. Обновляю...")
                        db_utils.supabase.table("products").update({"pv": catalog_pv}).eq("id", product_id).execute()
                        updated_count += 1
                        time.sleep(0.1) # Небольшая пауза

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке строки #{row_idx+1} в таблице #{table_idx+1}: {e}", exc_info=True)

    logger.info(f"🎉 Процесс завершен. Всего обработано товаров из каталога: {processed_count}.")
    if updated_count > 0:
        logger.info(f"✅ Успешно обновлено {updated_count} товаров с недостающими значениями PV.")
    else:
        logger.info("✅ Не найдено товаров, требующих обновления. Все данные в базе актуальны.")


if __name__ == "__main__":
    logger.info("Запуск служебного скрипта для заполнения PV...")
    backfill_missing_pv(DOCX_FILE)