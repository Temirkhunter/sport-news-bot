# ТЗ: Автоматизация Telegram-канала спортивных новостей

> Документ для Claude Code. Реализовать систему end-to-end по этой спецификации.

---

## 1. Цель проекта

Создать автономную Python-систему, которая:

- Мониторит закрытые/публичные источники (Telegram + ВКонтакте) на наличие новых спортивных постов
- Дополнительно собирает актуальные футбольные новости из веб-источников (RSS)
- Через OpenRouter (LLM) переписывает контент в уникальные посты
- Генерирует тематическое изображение (AI или стоковое — бесплатно)
- Публикует готовый пост в целевой Telegram-канал
- Работает по расписанию без участия оператора

---

## 2. Источники контента

### 2.1 Основные источники для мониторинга

| Источник | Тип | URL |
|---|---|---|
| Goalkz Official | VK | https://vk.com/goalkz_official |
| KPL 2017 | VK | https://vk.com/kpl_2017 |
| Eldar Amanvibe | Telegram | https://t.me/eldar_amanvibe |

### 2.2 Дополнительные источники (футбол)

- RSS / API: BBC Sport, ESPN, Goal.com, Sports.ru, Championat.com
- Конфигурируемый список в `config/sources.yaml` для лёгкого расширения новыми источниками без правки кода

---

## 3. Архитектура

```
┌─────────────────┐
│  Scheduler      │  (APScheduler, каждые 15–30 мин)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Collectors (parallel, async)           │
│  ├─ TelegramCollector (Telethon)        │
│  ├─ VKCollector (vk_api)                │
│  └─ WebCollector (feedparser)           │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│  Deduplicator   │  (SQLite: hash, source_id, posted_at)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  LLM Rewriter   │  (OpenRouter API)
│  - rewrite text │
│  - extract tags │
│  - image prompt │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Image Provider (fallback chain)        │
│  1. Pollinations.ai (free AI, no key)   │
│  2. Together AI free tier (FLUX)        │
│  3. Unsplash API (stock fallback)       │
│  4. Pexels API (stock fallback)         │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│  TG Publisher   │  (python-telegram-bot)
└─────────────────┘
```

---

## 4. Технологический стек

- **Язык:** Python 3.11+
- **Telegram чтение:** Telethon (авторизация по номеру телефона)
- **Telegram публикация:** `python-telegram-bot` (через Bot API)
- **VK:** `vk_api` (требуется `access_token` с правами `wall,offline`)
- **LLM:** OpenRouter API
  - Основная модель: `meta-llama/llama-3.3-70b-instruct:free`
  - Резервная: `deepseek/deepseek-chat`
- **БД:** SQLite (дедупликация, история постов)
- **Планировщик:** APScheduler
- **HTTP:** `httpx` (async)
- **Парсинг RSS:** `feedparser`
- **Конфиги:** `pydantic-settings` + YAML
- **Логирование:** `loguru`
- **Деплой:** Docker + docker-compose

---

## 5. Структура проекта

```
sport-news-bot/
├── config/
│   ├── sources.yaml          # список источников
│   ├── prompts.yaml          # промпты для LLM
│   └── settings.py           # pydantic-конфиг
├── src/
│   ├── collectors/
│   │   ├── base.py
│   │   ├── telegram.py
│   │   ├── vk.py
│   │   └── web.py
│   ├── processors/
│   │   ├── deduplicator.py
│   │   ├── rewriter.py       # OpenRouter
│   │   └── image_gen.py      # fallback chain
│   ├── publisher/
│   │   └── telegram_pub.py
│   ├── db/
│   │   ├── models.py
│   │   └── repository.py
│   ├── scheduler.py
│   └── main.py
├── data/
│   ├── posts.db
│   └── images/
├── logs/
├── .env.example
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 6. Логика работы модулей

### 6.1 Collectors

#### TelegramCollector (Telethon)

- Авторизация по сессии (один раз при первом запуске через CLI-флаг `--init-telegram-session`)
- Метод `fetch_new(channel, since_id)` возвращает посты с `message_id > last_seen`
- Извлекает: текст, медиа (URL), дату публикации, ссылку на оригинал

#### VKCollector

- Использует `wall.get` через `vk_api`
- Параметры: `domain`, `count=10`
- Извлекает: текст, attachments (photos), date

#### WebCollector

- Парсит RSS-фиды из `sources.yaml`
- Фильтр по ключевым словам (футбол, спорт, KPL, и т. д.)

### 6.2 Deduplicator

- SHA256-хеш от нормализованного текста (lowercase, без пунктуации, первые 200 символов)
- Проверка по таблице `processed_posts`
- TTL записей — 30 дней

### 6.3 LLM Rewriter (OpenRouter)

**Промпт-шаблон** (`config/prompts.yaml`):

```yaml
rewrite:
  system: |
    Ты — редактор русскоязычного спортивного Telegram-канала.
    Твоя задача: переписать новость в живом, динамичном стиле,
    сохранив все факты, цифры, имена. Объём: 400–800 знаков.
    Добавь 3–5 релевантных хэштегов в конце.
    Используй эмодзи умеренно (2–4 на пост).
    НЕ выдумывай факты. НЕ добавляй мнения.
  user: |
    Исходная новость:
    {original_text}

    Источник: {source}

    Верни JSON:
    {
      "text": "переписанный пост",
      "image_prompt": "англоязычный промпт для генерации изображения (предметно, без лиц реальных людей)",
      "hashtags": ["#tag1", "#tag2"],
      "category": "football|basketball|other"
    }
```

- Модель по умолчанию: `meta-llama/llama-3.3-70b-instruct:free`
- Резервная: `deepseek/deepseek-chat`
- Retry: 3 попытки с экспоненциальной задержкой
- Валидация JSON-ответа через `pydantic`; при невалидном — повторный запрос с уточнённым промптом, потом fallback-модель

### 6.4 Image Provider (бесплатные опции)

Цепочка fallback:

1. **Pollinations.ai** — полностью бесплатно, без ключа

   ```
   GET https://image.pollinations.ai/prompt/{url_encoded_prompt}?width=1024&height=1024&nologo=true
   ```

2. **Together AI** — free tier, модель `black-forest-labs/FLUX.1-schnell-Free`. Требует ключ, есть лимиты.

3. **Unsplash API** — стоковые фото. 50 запросов/час на free tier. Поиск по `category` + ключевым словам из `image_prompt`.

4. **Pexels API** — резерв, 200 запросов/час.

**Правила выбора:**

- Если в посте категория `football` → искать "soccer match", "football stadium"
- Если в тексте есть имя реального спортсмена → **НЕ** генерировать AI-портрет, использовать сток
- Сохранение локально в `data/images/` с TTL 7 дней

### 6.5 Publisher

- Отправка через `sendPhoto` с caption (если caption ≤ 1024 символов)
- Иначе: `sendPhoto` без подписи + отдельный `sendMessage` с полным текстом
- В конце поста: ссылка на оригинал (опционально, флаг `ADD_SOURCE_LINK` в конфиге)
- Rate limiting: не более 1 поста в 30 секунд

---

## 7. Конфигурация (`.env`)

```env
# Telegram (чтение через Telethon)
TG_API_ID=
TG_API_HASH=
TG_PHONE=
TG_SESSION_NAME=sport_bot

# Telegram (публикация)
TG_BOT_TOKEN=
TG_TARGET_CHANNEL=@your_channel

# VK
VK_ACCESS_TOKEN=
VK_API_VERSION=5.199

# OpenRouter
OPENROUTER_API_KEY=
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
OPENROUTER_FALLBACK_MODEL=deepseek/deepseek-chat

# Images (все опциональны кроме Pollinations, который без ключа)
TOGETHER_API_KEY=
UNSPLASH_ACCESS_KEY=
PEXELS_API_KEY=

# Schedule
CHECK_INTERVAL_MINUTES=20
POST_DELAY_SECONDS=30
MAX_POSTS_PER_RUN=3

# Behavior
DRY_RUN=false
ADD_SOURCE_LINK=false
ADMIN_CHAT_ID=
```

---

## 8. Схема БД (SQLite)

```sql
CREATE TABLE processed_posts (
  id INTEGER PRIMARY KEY,
  source_type TEXT,        -- telegram | vk | web
  source_id TEXT,          -- channel/group identifier
  external_id TEXT,        -- message_id или post_id
  content_hash TEXT UNIQUE,
  original_text TEXT,
  rewritten_text TEXT,
  image_path TEXT,
  status TEXT,             -- pending | published | failed
  created_at DATETIME,
  published_at DATETIME
);

CREATE INDEX idx_hash ON processed_posts(content_hash);
CREATE INDEX idx_source ON processed_posts(source_type, source_id, external_id);
```

---

## 9. Конфиг источников (`config/sources.yaml`)

```yaml
telegram:
  - name: eldar_amanvibe
    channel: "@eldar_amanvibe"
    enabled: true

vk:
  - name: goalkz_official
    domain: goalkz_official
    enabled: true
  - name: kpl_2017
    domain: kpl_2017
    enabled: true

web:
  - name: sports_ru_football
    url: "https://www.sports.ru/rss/football.xml"
    keywords: ["футбол", "матч", "гол", "лига"]
    enabled: true
  - name: championat_football
    url: "https://www.championat.com/rss/football/0/news.xml"
    enabled: true
```

---

## 10. Запуск и развёртывание

**Локально:**

```bash
# Первая авторизация в Telegram (один раз)
python -m src.main --init-telegram-session

# Обычный запуск с планировщиком
python -m src.main

# Один прогон без планировщика (для теста)
python -m src.main --once
```

**Docker:**

```bash
docker-compose up -d
docker-compose logs -f
```

---

## 11. Обработка ошибок и устойчивость

- Все сетевые запросы — с retry (`tenacity`) и таймаутами (10 сек по умолчанию)
- Если LLM не вернул валидный JSON — повторный запрос, затем fallback-модель
- Если все image-провайдеры упали — публикуем пост без картинки + флаг в логах
- Graceful shutdown по SIGTERM: доделать текущий цикл, потом выйти
- Rate limiting: не более 1 поста в 30 секунд в Telegram (Bot API лимит)

---

## 12. Логи и мониторинг

- `loguru` пишет в `logs/bot_{date}.log` с ротацией (10 МБ, хранение 14 дней)
- Уровни:
  - `DEBUG` — детали парсинга источников
  - `INFO` — публикации, успешные итерации
  - `ERROR` — исключения, упавшие запросы
- Опционально: отправка критических ошибок в `ADMIN_CHAT_ID` (приватный чат с ботом)

---

## 13. Credentials, которые нужно получить перед стартом

1. **Telegram API** — https://my.telegram.org → `api_id`, `api_hash`
2. **Telegram Bot** — через `@BotFather`, добавить бота в целевой канал как админа с правом публикации
3. **VK access token** — через https://vkhost.github.io (права: `wall`, `offline`)
4. **OpenRouter API key** — https://openrouter.ai/keys
5. **(опционально)** Unsplash, Pexels, Together AI — для расширения цепочки изображений

---

## 14. План реализации для Claude Code

Выполнять последовательно, коммитить после каждого пункта:

1. Скаффолд проекта: структура папок, `requirements.txt`, `.env.example`, `.gitignore`
2. Модели БД (`src/db/models.py`) + инициализация SQLite
3. `TelegramCollector` + тест на канале `@eldar_amanvibe`
4. `VKCollector` + тест на пабликах `goalkz_official`, `kpl_2017`
5. `WebCollector` (RSS) + базовый `sources.yaml`
6. `Deduplicator` + интеграционный тест
7. `Rewriter` (OpenRouter) + промпты + JSON-валидация через pydantic
8. `ImageProvider` со всей fallback-цепочкой
9. `Publisher` + rate limiting
10. `Scheduler` (APScheduler) + main loop
11. `Dockerfile` + `docker-compose.yml`
12. `README.md` с пошаговой инструкцией запуска

---

## 15. Acceptance criteria

Система считается готовой, когда:

- При первом запуске успешно проходит авторизация в Telegram
- За один прогон собираются посты из всех трёх основных источников + минимум одного RSS
- Дубликаты не публикуются повторно
- LLM возвращает валидный JSON с переписанным текстом и image_prompt
- Минимум один из image-провайдеров отдаёт изображение
- Пост успешно публикуется в целевой канал с картинкой
- Система работает по расписанию минимум 24 часа без падений
- В логах видно полный путь каждого поста: collect → dedup → rewrite → image → publish
