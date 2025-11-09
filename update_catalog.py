import os
import re
import uuid
from typing import List, Tuple

from docx import Document
from supabase import create_client, Client
from dotenv import load_dotenv

# ===================== #
#   Загрузка .env       #
# ===================== #
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "products")
DOCX_FILE = os.getenv("DOCX_FILE", "catalog.docx")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL и/или SUPABASE_KEY не заданы. Проверь .env")

# Инициализация клиента
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ===================== #
#   Утилиты             #
# ===================== #
def clean_price(text: str) -> int:
    """
    Нормализуем цену из ячейки:
    Примеры входа: '4 600 тг', '4 600', '4600', '4,600', '4 600 KZT'
    Возвращаем целое число в тинах/тенге (без копеек)
    """
    if not text:
        return 0
    # оставляем цифры, пробелы, запятую и точку
    t = re.sub(r"[^\d.,\s]", "", text)
    # убираем пробелы/узкие пробелы
    t = t.replace(" ", "").replace("\u202f", "")
    # запятую трактуем как точку
    t = t.replace(",", ".")
    # если есть точка — берём целую часть до точки
    if "." in t:
        t = t.split(".", 1)[0]
    return int(t) if t.isdigit() else 0

def clean_pv(text: str) -> float:
    """
    Нормализуем PV (баллы).
    Примеры: '10', '10.5', '10,5 PV'
    """
    if not text:
        return 0.0
    t = re.sub(r"[^\d.,]", "", text)  # оставляем только цифры и разделители
    t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def guess_ext_and_mime(content_type: str, partname: str) -> Tuple[str, str]:
    """
    По docx-part определяем расширение и content-type.
    """
    # пример content_type: 'image/jpeg' или 'image/png'
    ext = ""
    mime = content_type or "application/octet-stream"

    if partname:
        # partname выглядит как '/word/media/image1.png'
        _, ext_candidate = os.path.splitext(str(partname))
        if ext_candidate:
            ext = ext_candidate.lower().lstrip(".")

    if not ext:
        if mime == "image/jpeg":
            ext = "jpg"
        elif mime == "image/png":
            ext = "png"
        elif mime == "image/gif":
            ext = "gif"
        elif mime in ("image/webp",):
            ext = "webp"
        else:
            ext = "jpg"  # дефолт

    return ext, mime


def upload_image_to_supabase(image_bytes: bytes, orig_filename: str, content_type: str = None) -> str:
    """
    Загрузка изображения в Supabase Storage, возврат публичного URL.
    """
    # Генерируем уникальное имя
    path = f"{uuid.uuid4()}_{orig_filename}"
    options = None
    if content_type:
        options = {"content-type": content_type}

    # Если файл с таким именем случайно уже есть — не хотим падать
    try:
        supabase.storage.from_(SUPABASE_BUCKET).remove([path])
    except Exception:
        pass

    supabase.storage.from_(SUPABASE_BUCKET).upload(path, image_bytes, file_options=options)
    # Публичный URL (bucket должен быть public, а политика SELECT разрешена)
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"


def extract_images_from_cell(cell, doc) -> List[str]:
    """
    Извлекаем все картинки из ячейки таблицы и грузим их в Storage.
    Возвращаем список публичных URL.
    """
    image_urls: List[str] = []
    # Ищем blip'ы (встроенные изображения)
    blips = cell._element.xpath(".//a:blip")
    for blip in blips:
        rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        if not rid:
            continue
        image_part = doc.part.related_parts[rid]
        image_bytes = image_part.blob
        content_type = getattr(image_part, "content_type", None)
        partname = getattr(image_part, "partname", None)

        ext, mime = guess_ext_and_mime(content_type, str(partname))
        filename = f"product.{ext}"
        url = upload_image_to_supabase(image_bytes, filename, mime)
        image_urls.append(url)
    return image_urls


# Стало:
def upsert_product(name: str, description: str, price: int, images: List[str], pv: float, search_tags: str = None):
    """
    Обновляем товар. Добавлено поле search_tags с дефолтным значением None.
    """
    if not name:
        return

    existing = supabase.table("products").select("id").eq("name", name).execute()
    data = {
        "name": name,
        "description": description,
        "price": price,
        "images": images,
        "pv": pv,
        # 💡 ВАЖНО: Добавляем новое поле в data
        "search_tags": search_tags 
    }

    if existing.data:
        supabase.table("products").update(data).eq("id", existing.data[0]["id"]).execute()
        print(f"✅ Обновлено: {name} (фото: {len(images)}, PV={pv})")
    else:
        supabase.table("products").insert(data).execute()
        print(f"➕ Добавлено:  {name} (фото: {len(images)}, PV={pv})")


def parse_word_and_upload(docx_path: str):
    """
    Ожидается таблица с колонками:
      [0] Фото, [1] Название, [2] Описание, [3] Цена, [4] PV
    """
    print(f"📄 Читаю: {docx_path}")
    doc = Document(docx_path)

    total_rows = 0
    processed = 0

    for table in doc.tables:
        for i, row in enumerate(table.rows):
            if i == 0:
                maybe_header = " ".join([c.text.lower() for c in row.cells])
                if any(k in maybe_header for k in ["назв", "опис", "цена", "pv"]):
                    continue

            total_rows += 1

            try:
                name = (row.cells[1].text or "").strip()
                description = (row.cells[2].text or "").strip()
                price_text = (row.cells[3].text or "").strip()
                pv_text = (row.cells[4].text or "").strip()
            except IndexError:
                continue

            if not name:
                continue

            images = extract_images_from_cell(row.cells[0], doc)
            price = clean_price(price_text)
            pv = clean_pv(pv_text)

            upsert_product(name=name, description=description, price=price, images=images, pv=pv)
            processed += 1

    print(f"✅ Готово. Обработано строк: {processed}/{total_rows}")



if __name__ == "__main__":
    parse_word_and_upload(DOCX_FILE)
