-- =================================================================
-- СХЕМА БАЗЫ ДАННЫХ ДЛЯ ПРОЕКТА GREENLEAF AI BOT
-- =================================================================
-- Платформа: Supabase (PostgreSQL)
--
-- Инструкция по применению:
-- 1. В панели управления Supabase перейдите в раздел "Database" -> "Extensions".
-- 2. Убедитесь, что расширение "vector" включено. Если нет, включите его.
-- 3. Перейдите в "SQL Editor" -> "New query".
-- 4. Скопируйте и выполните весь код из этого файла.
-- =================================================================

-- 1. Таблица для хранения товаров (products)
-- Здесь будут храниться все данные о продуктах, включая их векторные представления (эмбеддинги).
create table public.products (
  id bigserial primary key,
  name text not null,
  description text,
  price numeric,
  images text,          -- Хранит URL изображения или JSON-массив URL-ов
  pv int,               -- "Personal Volume" или другие баллы
  search_tags text,     -- Сгенерированные LLM теги для улучшения поиска
  embedding vector(1536), -- Вектор для семантического поиска (модель text-embedding-3-small)
  created_at timestamptz default now()
);

-- 2. Таблица для хранения пользователей Telegram (users)
-- Сохраняет информацию о пользователях, которые взаимодействовали с ботом.
create table public.users (
  user_id bigint primary key, -- ID пользователя из Telegram
  first_name text,
  last_name text,
  username text,
  last_search_results jsonb,  -- Хранит JSON с последними найденными товарами для контекста
  created_at timestamptz default now()
);

-- 3. Таблица для истории сообщений (messages)
-- Логирует диалоги для поддержки контекста в разговорах с LLM.
create table public.messages (
  id bigserial primary key,
  user_id bigint references public.users(user_id) on delete cascade, -- Связь с пользователем
  role text not null, -- 'user' или 'assistant'
  content text,
  created_at timestamptz default now()
);

-- 5. Таблица для фрагментов каталога (chunks)
-- Здесь хранятся небольшие фрагменты текста из каталога для точного RAG-поиска.
create table public.catalog_chunks (
  id bigserial primary key,
  product_id bigint references public.products(id) on delete cascade, -- Связь с конкретным товаром
  content text not null, -- Текст фрагмента
  embedding vector(1536), -- Вектор фрагмента
  created_at timestamptz default now()
);

-- Индекс для ускорения векторного поиска по фрагментам
create index on public.catalog_chunks using ivfflat (embedding vector_cosine_ops)
with
  (lists = 100);

-- 4. Функция для векторного поиска (match_products)
-- Эта функция позволяет выполнять семантический поиск по эмбеддингам.
create or replace function match_products (
  query_embedding vector(1536),
  match_count int
)
returns table (
  id bigint,
  name text,
  description text,
  price numeric,
  images text,
  search_tags text,
  similarity float
)
language sql stable
as $$
  select
    p.id,
    p.name,
    p.description,
    p.price,
    p.images,
    p.search_tags,
    1 - (p.embedding <=> query_embedding) as similarity
  from public.products as p
  where p.embedding is not null
  order by p.embedding <=> query_embedding
  limit match_count;
$$;

-- 6. НОВАЯ функция для поиска по фрагментам (match_chunks)
-- Ищет наиболее релевантные фрагменты текста по всему каталогу.
create or replace function match_chunks (
  query_embedding vector(1536),
  match_count int
)
returns table (
  id bigint,
  product_id bigint,
  content text,
  similarity float
)
language sql stable
as $$
  select
    cc.id,
    cc.product_id,
    cc.content,
    1 - (cc.embedding <=> query_embedding) as similarity
  from public.catalog_chunks as cc  
  order by cc.embedding <=> query_embedding
  limit match_count;
$$;

-- 7. НОВАЯ функция для получения товаров по списку ID
-- Нужна для финального шага RAG-поиска.
create or replace function get_products_by_ids(p_ids bigint[])
returns table (
  id bigint,
  name text,
  description text,
  price numeric,
  images text,
  pv int,
  search_tags text
)
language sql stable
as $$
  select
    p.id, p.name, p.description, p.price, p.images, p.pv, p.search_tags
  from public.products as p
  where p.id = any(p_ids);
$$;
-- 8. ИСПРАВЛЕННАЯ функция для поиска по ключевым словам
-- Ищет прямое вхождение слов в названии и тегах. Переписана на 'sql' для стабильности.
-- 💡 ИЗМЕНЕНО: Принимает массив слов и ищет товары, содержащие ВСЕ эти слова.
create or replace function keyword_search_products(
  search_terms text[]
)
returns setof public.products
language sql stable
as $$
  SELECT *
  FROM public.products
  WHERE (
    SELECT bool_and(name ILIKE '%' || term || '%' OR search_tags ILIKE '%' || term || '%')
    FROM unnest(search_terms) as term
  );
$$;