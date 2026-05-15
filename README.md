# Sport News Bot

Автономная Python-система для автоматизации Telegram-канала спортивных новостей.

**Что делает:**
- Мониторит Telegram-каналы (Telethon), VK-стены (vk_api) и RSS-фиды футбольных СМИ.
- Дедуплицирует входящие посты по SHA256-хешу нормализованного текста.
- Переписывает контент через OpenRouter LLM (`llama-3.3-70b` → fallback `deepseek-chat`).
- Генерирует/подбирает изображение по fallback-цепочке: Pollinations.ai → Together AI (FLUX) → Unsplash → Pexels.
- Публикует готовый пост в целевой Telegram-канал по расписанию.

---

## Установка

### Локально

```bash
python -m venv .venv
.\.venv\Scripts\activate          # Windows
# source .venv/bin/activate       # Linux/Mac
pip install -r requirements.txt
cp .env.example .env              # на Windows: copy .env.example .env
# заполнить .env (см. ниже)
```

### Docker

```bash
docker-compose up -d --build
docker-compose logs -f
```

---

## Конфигурация `.env`

Минимально необходимо:

| Параметр | Описание | Где взять |
|---|---|---|
| `TG_BOT_TOKEN` | Токен бота-публикатора | [@BotFather](https://t.me/BotFather) |
| `TG_TARGET_CHANNEL` | `@username` канала или `-100…` ID | бот должен быть админом канала |
| `OPENROUTER_API_KEY` | ключ для LLM | https://openrouter.ai/keys |

Опционально (без них соответствующие коллекторы пропускаются):

| Параметр | Для чего |
|---|---|
| `TG_API_ID`, `TG_API_HASH`, `TG_PHONE` | чтение Telegram через Telethon. [my.telegram.org](https://my.telegram.org) |
| `VK_ACCESS_TOKEN` | чтение VK-стен. [vkhost.github.io](https://vkhost.github.io), права `wall,offline` |
| `TOGETHER_API_KEY` | FLUX-генерация (free tier). https://api.together.xyz |
| `UNSPLASH_ACCESS_KEY` | сток. https://unsplash.com/developers |
| `PEXELS_API_KEY` | резервный сток. https://www.pexels.com/api/ |

Pollinations.ai работает **без ключа** — если других провайдеров не настроено, изображения всё равно будут.

---

## Запуск

### 1. Первая авторизация Telethon (если используется Telegram-чтение)

```bash
python -m src.main --init-telegram-session
```

Будет интерактивный ввод кода из SMS. Сессия сохранится в `sessions/`.

### 2. Один прогон (для проверки)

```bash
python -m src.main --once
```

### 3. Боевой режим с планировщиком

```bash
python -m src.main
```

Цикл будет запускаться каждые `CHECK_INTERVAL_MINUTES` минут (по умолчанию 20).

### Docker

```bash
# Боевой запуск
docker-compose up -d

# Логи
docker-compose logs -f

# Остановка (с graceful shutdown текущего цикла)
docker-compose down

# Авторизация Telethon внутри контейнера (если нужно):
docker-compose run --rm sport-news-bot python -m src.main --init-telegram-session
```

---

## Структура проекта

```
sport-news-bot/
├── config/
│   ├── settings.py        # pydantic settings (читает .env)
│   ├── sources.yaml       # список источников
│   └── prompts.yaml       # промпты LLM
├── src/
│   ├── collectors/        # telegram, vk, web (RSS)
│   ├── processors/        # deduplicator, rewriter (OpenRouter), image_gen
│   ├── publisher/         # telegram_pub
│   ├── db/                # SQLAlchemy модели + repository
│   ├── pipeline.py        # collect → dedup → rewrite → image → publish
│   ├── scheduler.py       # APScheduler
│   └── main.py            # CLI вход
├── data/
│   ├── posts.db           # SQLite
│   └── images/            # кеш картинок (TTL 7 дней)
├── logs/                  # bot_YYYY-MM-DD.log (ротация)
├── sessions/              # Telethon-сессии
├── .env                   # секреты
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Настройка источников

Редактируется `config/sources.yaml` — добавление/отключение источников без правки кода.

```yaml
telegram:
  - name: eldar_amanvibe
    channel: "@eldar_amanvibe"
    enabled: true

vk:
  - name: goalkz_official
    domain: goalkz_official
    enabled: true

web:
  - name: sports_ru_football
    url: "https://www.sports.ru/rss/football.xml"
    keywords: ["футбол", "матч", "гол"]   # опциональный фильтр
    enabled: true
```

---

## Логи и мониторинг

- `logs/bot_YYYY-MM-DD.log` — DEBUG, ротация 10 МБ, хранение 14 дней.
- Stderr — `INFO` по умолчанию (меняется через `LOG_LEVEL` в `.env`).
- Каждый пост в логах виден целиком: `collect → dedup → rewrite → image → publish`.
- Опционально критические ошибки шлются в `ADMIN_CHAT_ID`.

---

## Поведение и тонкости

- **Rate limiting** — не чаще 1 поста в `POST_DELAY_SECONDS` секунд (Bot API лимит).
- **MAX_POSTS_PER_RUN** — отсечка постов на цикл (по умолчанию 3), чтобы не «спамить» канал при большой пачке свежих новостей.
- **DRY_RUN=true** — бот собирает и переписывает, но не отправляет в канал.
- **AI vs сток**: если в переписанном тексте упоминаются имена реальных людей (по эвристике), используется только стоковый источник, без генерации портретов.
- **Caption > 1024 символов** — фото идёт отдельно от полного текста.
- **Graceful shutdown** — SIGTERM/SIGINT доделывают текущий цикл и выходят (на Windows — только SIGINT).

---

## Acceptance checklist

- [x] Telethon, vk_api, feedparser коллекторы
- [x] SHA256-дедупликация + SQLite
- [x] OpenRouter rewriter + pydantic-валидация + fallback
- [x] Image fallback chain (Pollinations → Together → Unsplash → Pexels)
- [x] Bot API публикация с rate limiting и обработкой длинных caption
- [x] APScheduler + graceful shutdown
- [x] Docker + docker-compose
- [x] CLI: `--once`, `--init-telegram-session`
