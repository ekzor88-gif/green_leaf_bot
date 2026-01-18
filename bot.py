import asyncio
import logging
import ast
import re
import time
from typing import Optional
from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, CommandObject # 💡 Добавили CommandObject для аргументов
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.enums.chat_action import ChatAction # <-- ДОБАВИТЬ ЭТОТ ИМПОРТ В НАЧАЛО ФАЙЛА

print("🚀 [BOT] Запуск: импорт модулей...")

# ❌ ИСПРАВЛЕНИЕ: Заменяем удаленный get_query_type на is_product_query
from llm import generate_answer, is_product_query 
print("✅ [BOT] Модуль LLM загружен.")

import config

import db 
print("✅ [BOT] Модуль DB загружен.")


logging.basicConfig(level=logging.INFO)
CATALOG_SHEET_URL = config.GOOGLE_SHEET_URL

bot = Bot(
    token=config.TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()
router = Router()
dp.include_router(router)

# 🛡️ НАСТРОЙКИ БЕЗОПАСНОСТИ
MAX_MESSAGE_LENGTH = 2000  # Максимальная длина сообщения (символов)
USER_LAST_MSG_TIME = {}    # Словарь для анти-спама {user_id: timestamp}

# --- Загрузка текста инструкции при старте ---
try:
    with open("USER_GUIDE.md", "r", encoding="utf-8") as f:
        USER_GUIDE_TEXT = f.read()
except FileNotFoundError:
    USER_GUIDE_TEXT = "К сожалению, инструкция не найдена."

# ----------------- КЛАВИАТУРЫ -----------------

def get_main_reply_keyboard():
    """Главное меню (постоянно отображаемое снизу)."""
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="⭐ Просмотреть каталог"),
                KeyboardButton(text="📞 Связь с менеджером")
            ],
            [
                KeyboardButton(text="📖 Инструкция")
            ],
        ],
        resize_keyboard=True, # Делает кнопки меньше
        one_time_keyboard=False # Кнопки не исчезают
    )
    return kb

def get_catalog_inline_keyboard():
    """Inline-кнопка для открытия URL каталога."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Открыть каталог в браузере", url=CATALOG_SHEET_URL)
            ]
        ]
    )
    return kb

def get_manager_keyboard(phone: str):
    """Inline-кнопка для связи с менеджером через WhatsApp."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📞 Связаться в WhatsApp",
                url=f"https://wa.me/{phone}"
            )
        ],
    ])
    return kb

def extract_price_from_query(text: str) -> Optional[float]:
    """
    Извлекает число, похожее на цену, из текста запроса.
    Ищет числа от 3 до 5 цифр.
    """
    # Ищем последовательность из 3-5 цифр, возможно с пробелами
    match = re.search(r'\b(\d[\d\s]{2,4}\d)\b', text)
    if not match:
        match = re.search(r'(\d{3,5})', text) # Попробуем найти просто число
    
    if match:
        try:
            return float(match.group(1).replace(" ", ""))
        except (ValueError, IndexError):
            return None
    return None
# ----------------- ОБРАБОТЧИКИ -----------------

@router.message(Command("start"))
async def on_start(message: Message, command: CommandObject):
    u = message.from_user
    # ⚠️ ОБЕРТКА DB: upsert_user
    await asyncio.to_thread(db.upsert_user, u.id, u.first_name or "", u.last_name or "", u.username or "")
    
    # 💡 ПРОВЕРКА РЕФЕРАЛЬНОЙ ССЫЛКИ (Deep Linking)
    # Если есть аргумент (например, /start partner1), пробуем привязать партнера
    args = command.args
    if args:
        # Запускаем привязку в фоне, не блокируя ответ
        await asyncio.to_thread(db.assign_partner_by_code, u.id, args)

    # Отправляем приветствие и постоянно видимое Reply-меню
    await message.answer(
        "🌿 <b>Добро пожаловать! Мы вас очень ждали.</b>\n\n"
        "Я — ваш личный помощник в мире эко-товаров GreenLeaf.\n\n"
        "Этот бот — авторская разработка <b>семьи Артемьевых</b>, партнеров компании в г. Щучинск. Мы вложили сюда свой опыт, чтобы вы могли находить любимые товары за секунды.\n\n"
        "✍️ <b>Как это работает?</b>\n"
        "Не нужно листать длинные каталоги. Просто напишите в чат, что вы ищете:\n"
        "— <i>Чай для похудения</i>\n"
        "— <i>Шампунь от выпадения</i>\n"
        "— <i>Гель для стирки</i>\n\n"
        "Попробуйте прямо сейчас! 👇\n"
        "А еще подумайте, какую покупку вы бы хотели сделать в ближайшее время, возможно тут вы найдете что-то интересное.\n\n",
        reply_markup=get_main_reply_keyboard() # <-- Используем Reply Keyboard
    )

# Обработчик команды /help
@router.message(Command("help"))
async def on_help(message: Message):
    # Отправляем текст инструкции без форматирования, чтобы Markdown-разметка отображалась как есть
    await message.answer(USER_GUIDE_TEXT, parse_mode=None)

# Обработчик нажатия на кнопку "Просмотреть каталог"
@router.message(F.text == "⭐ Просмотреть каталог")
async def handle_view_catalog_reply(message: Message):
    await message.answer(
        "Вот ссылка на наш полный и актуальный каталог. Вы можете просмотреть его прямо сейчас:",
        reply_markup=get_catalog_inline_keyboard()
    )

# Обработчик нажатия на кнопку "Связь с менеджером"
@router.message(F.text == "📞 Связь с менеджером")
async def handle_manager_reply(message: Message):
    # 💡 Получаем динамический номер
    phone = await asyncio.to_thread(db.get_manager_phone_for_user, message.from_user.id)
    
    await message.answer(
        "Вы можете связаться с нашим менеджером 👇",
        reply_markup=get_manager_keyboard(phone)
    )

# Обработчик нажатия на кнопку "Инструкция"
@router.message(F.text == "📖 Инструкция")
async def handle_guide_reply(message: Message):
    await message.answer(USER_GUIDE_TEXT, parse_mode=None)

# Обработчик для нетекстовых сообщений
@router.message(~F.text)
async def on_media(message: Message):
    """Отвечает на нетекстовые сообщения.""" 
    await message.answer(
        "Извините, я умею работать только с **текстовыми сообщениями**."
        "\n\nПожалуйста, отправьте мне свой вопрос текстом. 👇"
    )


# ====================================================================
# --- ОБНОВЛЕННАЯ ФУНКЦИЯ on_text (БЕЗ ЛОГИКИ 'health') ---
# ====================================================================

@router.message(F.text)
async def on_text(message: Message):
    
    # 🛡️ 1. АНТИ-СПАМ ПРОВЕРКА
    user_id = message.from_user.id
    current_time = time.time()
    last_time = USER_LAST_MSG_TIME.get(user_id, 0)

    # Если прошло меньше 2 секунд с последнего сообщения
    if current_time - last_time < 2.0:
        # Можно просто игнорировать или мягко предупредить (лучше игнорировать, чтобы не спамить в ответ)
        return 
    
    USER_LAST_MSG_TIME[user_id] = current_time

    # 🛡️ 2. ПРОВЕРКА ДЛИНЫ СООБЩЕНИЯ
    if len(message.text) > MAX_MESSAGE_LENGTH:
        await message.answer("Сообщение слишком длинное. Пожалуйста, сформулируйте вопрос короче.")
        return

    # ... (Оставим реакцию и typing_task без изменений)
    typing_task = asyncio.create_task(
        # ... (код для отправки "печатает")
        bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    )
    await asyncio.sleep(0.3) 
    
    try:
        # ... (код для установки реакции)
        await bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[types.ReactionTypeEmoji(emoji="🤩")] 
        )
    except Exception as e:
        logging.info(f"Не удалось установить реакцию: {e}")

    try:
        u = message.from_user
        text = (message.text or "").strip()
        if not text:
            return

        # ------------------------------------------------------------------
        # --- ПРЕДВАРИТЕЛЬНЫЕ ПРОВЕРКИ И СОХРАНЕНИЕ ---
        # ------------------------------------------------------------------

        # Проверка на прямой запрос менеджера
        if any(word in text.lower() for word in ["менеджер", "заказ", "связь", "оператор"]):
            # 💡 Получаем динамический номер
            phone = await asyncio.to_thread(db.get_manager_phone_for_user, u.id)
            await message.answer(
                "Вы можете связаться с нашим менеджером 👇",
                reply_markup=get_manager_keyboard(phone)
            )
            # 💡 ВАЖНО: При запросе менеджера очищаем контекст товаров, так как диалог окончен
            await asyncio.to_thread(db.clear_last_products, u.id)
            return

        # Сохранение пользователя и сообщения
        await asyncio.to_thread(db.upsert_user, u.id, u.first_name or "", u.last_name or "", u.username or "")
        await asyncio.to_thread(db.save_message, u.id, "user", text)

        # Получение истории диалога
        history = await asyncio.to_thread(db.get_recent_messages, u.id, limit=8)

        # --------------------------------------------------------
        # --- ШАГ 1: КЛАССИФИКАЦИЯ И RAG (ПРЯМОЙ ПОИСК) ---
        # --------------------------------------------------------
        
        do_rag_search = await asyncio.to_thread(is_product_query, text)

        # 💡 СТРАХОВКА: Если LLM считает, что это не товар, но в базе есть точное совпадение — ищем.
        # Это решает проблему, когда LLM думает, что "жидкое иглоукалывание" — это процедура, а не товар.
        if not do_rag_search:
            # Проверяем быстро, есть ли такой товар по точному вхождению
            exact_hits = await asyncio.to_thread(db.search_products_by_exact_match, text)
            if exact_hits:
                logging.info(f"🛡️ Сработала страховка: '{text}' найден в базе, хотя LLM классифицировала как не-товар.")
                do_rag_search = True


        # Инициализируем переменные для контекста
        products_for_text_gen = []
        chunks_for_text_gen = []
        # Эта переменная будет хранить товары ТОЛЬКО из нового поиска для отображения кнопок
        newly_matched_products = []

        if do_rag_search:
            # --- СЦЕНАРИЙ 1: ПОИСК ТОВАРА (RAG) ---
            
            # 1. Ищем товары и релевантные фрагменты.
            # 💡 ИЗМЕНЕНИЕ: search_products теперь возвращает (products, chunks)
            # 💡 КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Функция db.search_products уже возвращает ОБЪЕДИНЕННЫЙ список товаров.
            # Убираем лишнюю логику слияния, которая здесь больше не нужна.
            final_products, chunks_for_text_gen = await asyncio.to_thread(db.search_products, text)
            products_for_text_gen = final_products
            newly_matched_products = final_products # Этот список используется для кнопок

            # 💡 НОВЫЙ ШАГ: "ВТОРОЙ ШАНС" ДЛЯ ПОИСКА (если первый не сработал)
            if not newly_matched_products:
                logging.info(f"Прямой поиск не дал результатов. Ищу альтернативные варианты для: '{text}'")
                
                # Сценарий 1: Поиск по цене
                user_price = extract_price_from_query(text)
                if user_price:
                    logging.info(f"Найдена цена в запросе: {user_price}. Запускаю поиск по диапазону цен.")
                    candidate_products = await asyncio.to_thread(db.search_products_by_price_range, user_price)
                    if candidate_products:
                        products_for_text_gen = candidate_products
                        newly_matched_products = candidate_products
                        logging.info(f"Поиск по цене нашел {len(candidate_products)} товаров.")
                
                # Сценарий 2: Переформулирование запроса с помощью LLM
                if not newly_matched_products:
                    reformulated_query = await asyncio.to_thread(db.reformulate_query_with_llm, text)
                    if reformulated_query:
                        logging.info(f"Запрос переформулирован в: '{reformulated_query}'. Запускаю повторный поиск.")
                        final_products, chunks_for_text_gen = await asyncio.to_thread(db.search_products, reformulated_query)
                        products_for_text_gen = final_products
                        newly_matched_products = final_products

                # Сценарий 3: Широкий поиск по категории (если все остальное не сработало)
                if not newly_matched_products:
                    logging.info(f"Переформулировка не помогла. Запускаю широкий поиск по категории для: '{text}'")
                    candidate_products = await asyncio.to_thread(db.filter_products_by_category, text)
                    if candidate_products:
                        products_for_text_gen = candidate_products
                        newly_matched_products = candidate_products # Отобразим кандидатов в кнопках
                        logging.info(f"Широкий поиск нашел {len(candidate_products)} кандидатов. Передаю их LLM для фильтрации.")

            # 2. Сохраняем ПОЛНЫЙ список товаров в Supabase для навигации
            await asyncio.to_thread(db.save_last_products, u.id, newly_matched_products)

        else:
            # --- СЦЕНАРИЙ 2: ПРОСТОЙ ДИАЛОГ (Проверка на продолжение контекста) ---
            
            # 💡 КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Если это не поисковый запрос, мы очищаем контекст,
            # чтобы бот не предлагал старые товары в ответ на "спасибо" или "нет".
            # Мы оставим контекст только если это уточняющий вопрос по списку.
            is_clarification = any(word in text.lower() for word in ["первый", "второй", "третий", "номер", "подробнее", "о нем"])
            if is_clarification:
                products_for_text_gen = await asyncio.to_thread(db.get_last_products, u.id)
            else:
                await asyncio.to_thread(db.clear_last_products, u.id)
                products_for_text_gen = []

        # 💡 ФИНАЛЬНАЯ ПРОВЕРКА: Если после всех поисков и фолбэков мы так и не нашли
        # ни одного товара, мы все равно сгенерируем ответ, но уже без контекста каталога.
        # Это позволит LLM дать общий совет, как в вашем примере.
        if not products_for_text_gen:
            logging.info("Ни один из поисковых механизмов не нашел товаров. Генерирую ответ без RAG-контекста.")
        # --------------------------------------------------------
        # --- ШАГ 2: ГЕНЕРАЦИЯ ОТВЕТА (ОБЩЕЕ) ---
        # --------------------------------------------------------
        
        # 💡 ИЗМЕНЕНИЕ: Вызываем LLM с правильными аргументами (products, chunks)
        answer = await asyncio.to_thread(
            generate_answer, 
            history_rows=history, 
            user_query=text, 
            products=products_for_text_gen, 
            chunks=chunks_for_text_gen
        )
        
        # --------------------------------------------------------
        # --- ШАГ 2: ОТВЕТ И ПОСТ-ОБРАБОТКА (ОБЩЕЕ) ---
        # --------------------------------------------------------
        
        if answer:
            # 💡 ГАРАНТИРОВАННОЕ ИСПРАВЛЕНИЕ: Принудительно заменяем Markdown на HTML-теги.
            # Это надежнее, чем полагаться на LLM.
            # Ищем все вхождения **текст** и заменяем на <b>текст</b>.
            answer = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', answer)
            await asyncio.to_thread(db.save_message, u.id, "assistant", answer)
            await message.answer(answer, parse_mode=ParseMode.HTML)

        # Вывод кнопок для товаров (только если был RAG-поиск и товары найдены)
        # Кнопки должны выводиться только после НОВОГО поиска.
        if do_rag_search and newly_matched_products:
            
            # ... (Остальной код вывода кнопок остается без изменений)
            total = len(newly_matched_products)
            
            if total <= 5:
                # 1. 1–5 товаров → сразу выводим все кнопки
                buttons = [
                    [InlineKeyboardButton(
                        text=f"{p['name']}",
                        callback_data=f"product_{p['id']}"
                    )]
                    for p in newly_matched_products
                ]
                
                header = "Я нашёл этот товар 👇" if total == 1 else f"Я нашёл {total} товаров 👇"
                
                await message.answer(
                    header,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
                )

            else:
                # 2. Больше 5 → предлагаем показать первые 5 или уточнить
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Показать первые 5", callback_data="show_page_0"),
                        InlineKeyboardButton(text="Нет, уточнить", callback_data="filter")
                    ]
                ])
                await message.answer(
                    f"Я нашёл еще {total} товаров, которые могу Вас заинтересовать. Показать первые 5 или уточним?",
                    reply_markup=kb
                )

    except Exception as e:
        logging.error(f"Ошибка в on_text: {e}")
        await message.answer("Упс, что-то пошло не так 🙏")
    
    # Отменяем задачу "Печатает..."
    if not typing_task.done():
        typing_task.cancel()

        
# ================== КОЛЛБЕКИ НАВИГАЦИИ ПО ТОВАРАМ ===================

@router.callback_query(F.data.startswith("show_page_"))
async def show_page(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    PAGE_SIZE = 5
    
    # Извлекаем текущий индекс/оффсет из callback_data (например, 'show_page_0' -> 0)
    try:
        current_offset = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка навигации: некорректный индекс.")
        return

    # ********** ИЗВЛЕКАЕМ ИЗ SUPABASE **********
    all_products = await asyncio.to_thread(db.get_last_products, user_id)
    total = len(all_products)

    if not all_products:
        await callback.message.edit_text("Извините, результаты поиска устарели или не найдены. Попробуйте начать новый поиск.")
        await callback.answer()
        return

    # Выбираем товары для текущей страницы
    products_on_page = all_products[current_offset : current_offset + PAGE_SIZE]
    
    # ----------------- КНОПКИ ТОВАРОВ -----------------
    buttons = [
        [InlineKeyboardButton(
            text=f" {p['name']}",
            # Здесь предполагается, что в элементах all_products есть 'id'
            callback_data=f"product_{p.get('id')}" 
        )]
        for p in products_on_page
    ]
    
    # ----------------- КНОПКИ НАВИГАЦИИ (ПОДВАЛ) -----------------
    
    footer_buttons = []
    
    next_offset = current_offset + PAGE_SIZE
    has_next = next_offset < total
    
    # Если есть еще товары, добавляем кнопку "Показать ещё"
    if has_next:
        footer_buttons.append(
            # 💡 ПЕРЕДАЕМ НОВЫЙ СТАРТОВЫЙ ИНДЕКС
            InlineKeyboardButton(text=f"Показать ещё ({total - next_offset})", callback_data=f"show_page_{next_offset}")
        )
    
    # Добавляем кнопку "Хватит" (или "Назад", если нужна)
    footer_buttons.append(
        InlineKeyboardButton(text="Хватит, вернуться", callback_data="stop")
    )
    
    # Формируем итоговую клавиатуру
    kb = InlineKeyboardMarkup(
        inline_keyboard=buttons + [footer_buttons]
    )

    # ----------------- ОТПРАВКА -----------------
    
    # Определяем текст для заголовка страницы
    if current_offset == 0:
        header_text = f"Первые {len(products_on_page)} из {total} товаров 👇"
    else:
        header_text = f"Показаны товары {current_offset + 1} - {current_offset + len(products_on_page)} из {total} 👇"
        
    await callback.message.edit_text(header_text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "stop")
async def stop_navigation(callback: types.CallbackQuery):
    await callback.message.edit_text("Хорошо, рад был помочь с выбором!")
    await callback.answer()


# ================== ПРОДУКТ ДЕТАЛИ (ИСПРАВЛЕНО) ===================

@router.callback_query(F.data.startswith("product_"))
async def on_product_detail(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    # Максимальные лимиты Telegram
    MAX_CAPTION_LENGTH = 1024
    MAX_MESSAGE_LENGTH = 4096
    
    try:
        product_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.message.answer("Ошибка: некорректный ID товара.")
        await callback.answer()
        return

    # ********** ИЗВЛЕКАЕМ ИЗ SUPABASE **********
    products = await asyncio.to_thread(db.get_last_products, user_id)
    
    # 💡 ИСПРАВЛЕНИЕ: Сравниваем ID как целые числа для надежности.
    # Это предотвратит ошибки, если product_id - int, а p.get("id") - str, и наоборот.
    product = next((p for p in products if p and p.get("id") is not None and int(p.get("id")) == product_id), None)

    
    if not product:
        # Уведомляем пользователя, но не удаляем сообщение, чтобы не вызвать ошибку
        await callback.answer("❌ Товар не найден. Возможно, результаты устарели.")
        return

    # ----------------- СБОР ДАННЫХ -----------------
    name = product.get("name") or product.get("Название") or "Без названия"
    price = product.get("price") 
    pv = product.get("pv")
    price_text = f"{price} тг" if price else "не указана"
    description = product.get("description") or product.get("Описание") or ""
    description = description.strip()
    
    # --- Поиск URL изображения (логика из вашего кода) ---
    # 💡 УПРОЩЕНИЕ: Оставляем только новую, чистую логику для работы с jsonb-полем 'images'.
    image_url = None
    images_field = product.get("images")
    if images_field:
        try:
            # 1. Если это уже список (JSONB распарсился автоматически)
            if isinstance(images_field, list) and images_field:
                image_url = images_field[0]
            # 2. Если это строка (TEXT или JSON в виде строки)
            elif isinstance(images_field, str) and images_field.startswith('['):
                images_list = ast.literal_eval(images_field)
                if isinstance(images_list, list) and images_list:
                    image_url = images_list[0]
        except (ValueError, SyntaxError):
            logging.warning(f"Не удалось распарсить поле images: {images_field}")

    # ----------------- ФОРМИРОВАНИЕ ТЕКСТА -----------------
    
    header_text = f"✨ <b>{name}</b>\n\n💰 Цена: {price_text}"
    if pv:
        header_text += f" |  баллы: {pv} pv"
    full_text = f"{header_text}\n\n{description}"
    
    # ----------------- ОТПРАВКА СООБЩЕНИЙ -----------------

    if image_url:
        # 1. Сценарий с фото: отправляем фото + caption (макс 1024 символа)
        caption_to_send = full_text[:MAX_CAPTION_LENGTH]
        
        try:
            await callback.message.answer_photo(
                photo=image_url, 
                caption=caption_to_send, 
                parse_mode=ParseMode.HTML
            )
            
            # Если описание длиннее 1024 символов, отправляем остаток отдельным сообщением.
            if len(full_text) > MAX_CAPTION_LENGTH:
                # Берем остаток текста
                remaining_description = full_text[MAX_CAPTION_LENGTH:]
                
                # Убеждаемся, что остаток не превышает лимит 4096 символов
                text_to_send = remaining_description[:MAX_MESSAGE_LENGTH]
                
                if text_to_send:
                    await callback.message.answer(
                        text=text_to_send, 
                        parse_mode=ParseMode.HTML
                    )

        except Exception as e:
            # Если фото не загрузилось (ошибка Telegram/URL), отправляем текст полностью
            logging.error(f"Ошибка загрузки фото: {e}")
            text_to_send = full_text[:MAX_MESSAGE_LENGTH]
            await callback.message.answer(
                text=text_to_send + "\n⚠️ **Ошибка загрузки фото.**", 
                parse_mode=ParseMode.HTML
            )
    else:
        # 2. Сценарий без фото: отправляем только текст (макс 4096 символов)
        text_to_send = full_text[:MAX_MESSAGE_LENGTH]
        await callback.message.answer(
            text=text_to_send, 
            parse_mode=ParseMode.HTML
        )

    # ----------------- КНОПКИ -----------------
    # 💡 ИЗМЕНЕНИЕ: Получаем динамический номер менеджера
    phone = await asyncio.to_thread(db.get_manager_phone_for_user, user_id)
    
    # Кнопка теперь сразу ведет на WhatsApp
    buttons = [
        [InlineKeyboardButton(
            text="📞 Связаться в WhatsApp",
            url=f"https://wa.me/{phone}"
        )]
    ]
    await callback.message.answer(
        "Хотите обсудить этот товар или сделать заказ? Напишите нашему менеджеру 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    # Обязательно подтверждаем callback, чтобы исчезли часы на кнопке
    await callback.answer()

async def main():
    print("🔄 [BOT] Запуск polling (ожидание сообщений)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())