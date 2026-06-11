Продолжаю по методичке: **Слой 4 (CLAUDE.md + субагенты + rules + skills + SPEC_TEMPLATE + MCP)** и **Слой 5 (финальный промпт сборки)**. Всё copy-paste ready.

---

# СЛОЙ 4 — ПАКЕТ КОНФИГУРАЦИИ

## Классификатор проекта (по методичке, Слой 3)

```
Продакшн AI-агенты для пользователей? → НЕТ (бот не вызывает LLM в рантайме)
  → субагент ai-agent-architect НЕ добавляется
Платежи в рантайме (Stripe/Stars/ЮKassa)? → НЕТ в MVP, ДА в v3
  → субагент payments-specialist отложен до v3
Внешние интеграции? → Google Sheets (gspread), Supabase, Telegram Bot API
Стек → Python 3.11 / aiogram 3 / Supabase (Postgres) / gspread / APScheduler
```

Итоговая команда субагентов: **database-architect, backend-engineer, bot-developer, qa-reviewer**.

---

## Файл: `CLAUDE.md` (≤120 строк)

```markdown
# TG Traffic Analytics — конфиг для AI-агентов

## Обзор
Telegram-бот сквозной аналитики трафика. Бот — админ в бесплатном канале и
платном чате. Ловит вступления (chat_member), связывает подписку и продажу
по tg_user_id, считает CPF/CAC/ROMI по источникам, синкает с Google Sheets.

## Стек
- Python 3.11, aiogram 3.x (async)
- Supabase (Postgres) через supabase-py, доступ под service_role
- gspread (Google Sheets, service account)
- APScheduler (cron-пересчёт метрик)
- Docker, webhook (polling допустим на старте)

## Архитектура
src/bot/handlers   — chat_member, команды, callbacks
src/bot/keyboards  — inline-клавиатуры
src/core           — config, supabase client, logging
src/services       — attribution, analytics, sheets_sync, health
src/main.py        — entrypoint, set_webhook(allowed_updates=...)
supabase/migrations— SQL-миграции

## Ключевые таблицы
accounts, sources, subscribers, events, customers, costs, settings
(полные схемы и RLS — в SPECIFICATION.md)

## Критичные правила проекта
1. allowed_updates ОБЯЗАН включать: message, callback_query, chat_member,
   chat_join_request, my_chat_member. Иначе вступления не приходят.
2. Атрибуция = FIRST-TOUCH. Поле attribution_locked=true → source_id НЕ
   перезаписывается при повторном join.
3. Источник нельзя получить задним числом — только в момент join.
   Fallback всегда source_id=NULL («Источник не определён»).
4. Все апдейты chat_member идемпотентны: UPSERT по UNIQUE(account_id, tg_user_id).
5. Метрики с делением: знаменатель 0 → возвращать «—», НЕ падать.
6. Бот ходит в Supabase под service_role (обходит RLS). RLS включён для v3.
7. Google Sheets: колонки «Расход», «Цена продукта» — ТОЛЬКО чтение из них,
   НЕ затирать при записи метрик. Батч-операции, backoff при 429.
8. Перед createChatInviteLink проверять права бота can_invite_users.
9. Никаких секретов в коде — только .env (BOT_TOKEN, SUPABASE_URL,
   SUPABASE_SERVICE_KEY, GOOGLE_CREDENTIALS_JSON).

## Команды бота
/start /setchannel /setpaid /swap /status
/newsource /sources /importlinks
/cost /costs
/stats [источник]

## Команды разработки
- Запуск: python -m src.main
- Миграции: применять SQL из supabase/migrations через Supabase SQL editor / CLI
- Тесты: pytest tests/
- Линт: ruff check src/

## Документы-источники
PROJECT_IDEA.md — продуктовый контекст
SPECIFICATION.md — детальные модули (источник истины для реализации)
```

---

## Субагенты `.claude/agents/`

### `.claude/agents/database-architect.md`

```markdown
---
name: database-architect
description: Используй для проектирования схемы Postgres/Supabase, написания
  SQL-миграций, RLS-политик, индексов, FK-связей и идемпотентных upsert-запросов.
tools: Read, Write, Edit, Bash, Glob, Grep
model: opus
---

# Роль
Старший архитектор БД на PostgreSQL и Supabase. Проектируешь схему для
Telegram-аналитики и пишешь идемпотентные миграции.

## Принципы
- Типы строго: uuid, bigint, text, numeric(12,2), timestamptz, jsonb, boolean, date.
- Каждая таблица: PK uuid DEFAULT gen_random_uuid(), created_at timestamptz DEFAULT now().
- FK с ON DELETE CASCADE для дочерних к accounts/sources.
- UNIQUE-ключи для идемпотентности: subscribers(account_id, tg_user_id),
  customers(account_id, tg_user_id), sources(account_id, name).
- RLS включён на всех таблицах; политики через подзапрос к accounts по auth.uid().
  service_role обходит RLS — это штатный режим бота.
- Индексы под частые выборки: (account_id, tg_user_id), (account_id, created_at), (source_id).

## Паттерны
- Идемпотентный upsert подписчика:
  INSERT ... ON CONFLICT (account_id, tg_user_id)
  DO UPDATE SET ... WHERE subscribers.attribution_locked = false;
- Миграция = один .sql файл с номером-префиксом в supabase/migrations.
- Денежные значения — numeric(12,2), НЕ float.

## Чеклист перед завершением
□ Все таблицы из SPECIFICATION.md созданы
□ RLS включён + политики для каждой таблицы
□ UNIQUE-ключи обеспечивают идемпотентность
□ Индексы созданы
□ Нет TODO/заглушек в SQL

## Интеграция
Перед написанием API передай backend-engineer финальные имена полей.
Используй Context7 MCP для актуального синтаксиса Postgres/Supabase.
```

### `.claude/agents/backend-engineer.md`

```markdown
---
name: backend-engineer
description: Используй для серверной логики: сервисы attribution, analytics,
  sheets_sync, health; интеграция supabase-py, gspread, APScheduler; работа
  с Telegram Bot API (createChatInviteLink, getChatMember, set_webhook).
tools: Read, Write, Edit, Bash, Glob, Grep
model: opus
---

# Роль
Старший backend-инженер на Python 3.11 (async). Реализуешь бизнес-логику
сквозной аналитики и интеграции вне слоя хендлеров.

## Принципы
- Вся логика — в src/services, хендлеры только вызывают сервисы.
- Supabase: клиент под SUPABASE_SERVICE_KEY (service_role).
- Атрибуция FIRST-TOUCH: при повторном join source_id НЕ менять (attribution_locked).
- Все деления защищены: знаменатель 0 → возвращать None/«—».
- Идемпотентность: операции по апдейтам chat_member безопасны при повторе.
- Никаких секретов в коде — только из .env через src/core/config.
- Backoff (экспоненциальный) для Telegram 429 и Google Sheets 429.

## Паттерны
- attribute_subscriber(account_id, tg_user_id, invite_name):
  найти source_id по invite_name → upsert subscribers → insert events.
- attribute_customer(account_id, tg_user_id):
  найти subscriber → source_id наследуется → upsert customers (entry_type
  'paid' если найден, иначе 'manual') → insert events.
- compute_metrics(account_id) -> list[SourceMetrics]: агрегации + защита от /0.
- sync_to_sheets: СНАЧАЛА читать колонки «Расход»/«Цена продукта», ПОТОМ
  записывать метрики, не затирая колонки ввода. batch_update.

## Чеклист перед завершением
□ Все сервисы из SPECIFICATION.md реализованы
□ Деления защищены от нуля
□ Атрибуция идемпотентна и first-touch
□ Sheets-синк не затирает колонки ввода
□ Секреты только из .env
□ Нет TODO-заглушек

## Интеграция
Имена полей БД бери у database-architect. Сигнатуры сервисов передавай
bot-developer. Используй Context7 MCP для актуального API aiogram 3, supabase-py, gspread.
```

### `.claude/agents/bot-developer.md`

```markdown
---
name: bot-developer
description: Используй для слоя aiogram 3 — хендлеры команд и chat_member,
  inline-клавиатуры, FSM онбординга, форматирование сообщений /stats,
  set_webhook с allowed_updates.
tools: Read, Write, Edit, Bash, Glob, Grep
model: sonnet
---

# Роль
Telegram bot-разработчик на aiogram 3. Реализуешь весь слой взаимодействия:
команды, обработку событий, клавиатуры, форматирование.

## Принципы
- aiogram 3 Router-архитектура; хендлеры тонкие — логика в src/services.
- set_webhook ОБЯЗАН: allowed_updates=["message","callback_query",
  "chat_member","chat_join_request","my_chat_member"].
- chat_member-хендлер различает join (left/kicked → member/restricted) и leave.
- Фильтрация по chat.id: free_channel_id vs paid_chat_id из accounts.
- /stats форматировать моноширинной таблицей (```), числа округлять.
- Состояния UI: загрузка / успех / пусто / ошибка — у каждой команды.
- my_chat_member-хендлер: отслеживать потерю прав бота → алерт владельцу.

## Паттерны
- /newsource <имя>: проверка can_invite_users → create_source() → ответ ссылкой.
- /setchannel /setpaid: FSM, пересланное сообщение или ввод id, проверка прав.
- chat_member free → attribute_subscriber(); paid → attribute_customer().
- inline-клавиатуры для /sources и /costs (Скопировать/Статистика/Удалить).

## Чеклист перед завершением
□ Все команды из CLAUDE.md реализованы
□ allowed_updates выставлены при старте
□ chat_member корректно ловит join/leave в обоих чатах
□ Все состояния UI обработаны
□ Понятные сообщения об ошибках (нет прав / нет источника / дубль)
□ Нет TODO-заглушек

## Интеграция
Сигнатуры сервисов бери у backend-engineer. Используй Context7 MCP для
актуального aiogram 3 (Router, filters, FSM, ChatMemberUpdated).
```

### `.claude/agents/qa-reviewer.md`

```markdown
---
name: qa-reviewer
description: Используй для code review, проверки безопасности, корректности
  RLS, идемпотентности атрибуции и защиты от деления на ноль. НЕ правит код —
  только описывает проблемы списком.
tools: Read, Bash, Glob, Grep
model: sonnet
---

# Роль
QA-ревьюер. Находишь проблемы и описываешь их — НЕ исправляешь код
(нет прав Write/Edit). Выдаёшь нумерованный список с файлом, строкой, риском.

## Что проверять
1. Безопасность: секреты только в .env, нет токенов/ключей в коде и логах.
2. allowed_updates содержит chat_member — иначе бот «слепой».
3. Атрибуция: first-touch соблюдён, source_id не перезаписывается.
4. Идемпотентность: повторный chat_member не создаёт дублей (UNIQUE).
5. Деление на ноль во всех метриках → возвращается «—», не падает.
6. Sheets-синк не затирает колонки ввода «Расход»/«Цена продукта».
7. RLS включён на всех таблицах; политики корректны.
8. Обработка Telegram/Sheets 429 (backoff).
9. Edge cases из SPECIFICATION.md покрыты.

## Формат вывода
Список: [Критично/Средне/Низко] — файл:строка — проблема — рекомендация.
Без правок кода. Если всё ок по пункту — отметить ✓.

## Интеграция
Используй Bash/Grep для поиска паттернов (например, секреты, деления).
Context7 MCP — для сверки с актуальным API при сомнениях.
```

---

## Rules `.claude/rules/`

### `.claude/rules/database.md`

```markdown
---
glob: "supabase/migrations/**/*.sql"
---
# Правила миграций
- Денежные поля — numeric(12,2), не float/real.
- Каждая таблица: RLS ENABLE + политики.
- Идемпотентность через UNIQUE(account_id, tg_user_id) на subscribers/customers.
- FK к accounts/sources — ON DELETE CASCADE.
- Имя файла: NNN_описание.sql (порядок применения).
```

### `.claude/rules/attribution.md`

```markdown
---
glob: "src/services/attribution*.py"
---
# Правила атрибуции
- FIRST-TOUCH: при повторном join source_id НЕ менять (attribution_locked=true).
- invite_name не найден в sources → source_id=NULL + лог, не падать.
- Все операции идемпотентны (ON CONFLICT DO ... WHERE).
- entry_type: 'paid' если найден подписчик, иначе 'manual'.
```

### `.claude/rules/handlers.md`

```markdown
---
glob: "src/bot/handlers/**/*.py"
---
# Правила хендлеров
- Хендлеры тонкие: только парсинг + вызов сервиса + ответ.
- chat_member: определять join/leave по переходу статусов.
- Фильтровать по chat.id (free vs paid) до записи.
- Каждой команде — состояния загрузка/успех/пусто/ошибка.
- Понятные тексты ошибок на русском.
```

### `.claude/rules/sheets.md`

```markdown
---
glob: "src/services/sheets*.py"
---
# Правила Google Sheets
- Колонки «Расход» и «Цена продукта» — read-only из таблицы, НЕ затирать.
- Сначала чтение ввода → upsert в БД → потом запись метрик.
- batch_update вместо поячеечной записи.
- Backoff при 429; не терять данные при сбое.
```

---

## Skills `.claude/skills/`

### `.claude/skills/implement-feature.md`

```markdown
# Skill: implement-feature
Используй при реализации новой фичи по SPEC_TEMPLATE.md.

Workflow:
1. Прочитай спецификацию фичи (6 блоков).
2. database-architect: миграции по «Модель данных».
3. backend-engineer: сервисы по «API и бизнес-логика».
4. bot-developer: хендлеры/клавиатуры по «Экраны».
5. Покрой «Крайние случаи».
6. qa-reviewer: ревью по чеклисту, без правок.
7. Исправь замечания → повтори ревью.
```

### `.claude/skills/create-migration.md`

```markdown
# Skill: create-migration
Создание SQL-миграции для Supabase.

Workflow:
1. Имя файла: supabase/migrations/NNN_описание.sql.
2. Типы строго (uuid/bigint/numeric/timestamptz/jsonb).
3. RLS ENABLE + политики.
4. UNIQUE для идемпотентности, индексы под выборки.
5. Проверь FK ON DELETE CASCADE.
6. Никаких DROP без явного запроса.
```

---

## Шаблон фичи `SPEC_TEMPLATE.md`

```markdown
# Спецификация фичи: [НАЗВАНИЕ]

## Описание
[Что делает, для кого, зачем]

## User Stories
[Как роль, я хочу действие, чтобы результат — минимум 3, + крайние случаи]

## Модель данных
[Таблицы, поля, типы (uuid/bigint/numeric/timestamptz/jsonb), FK, RLS, UNIQUE]

## API / Серверная логика
[Команды бота / Server-функции: вход → выход → ошибки]

## Экраны
[Сообщения, клавиатуры, состояния: загрузка/успех/пусто/ошибка]

## Бизнес-логика
[Правила, валидации, формулы, защита от деления на ноль]

## Крайние случаи
[Нет данных? API не отвечает? Дубль join? Потеря прав? Rate-limit?]

## Приоритет / Зависимости
[MVP / v2 / v3 — от каких модулей зависит]
```

---

## MCP-команды

```bash
# Context7 — актуальная документация aiogram 3, supabase-py, gspread, Postgres
claude mcp add context7 -- npx -y @upstash/context7-mcp

# Supabase — работа с БД (миграции, проверка схемы)
claude mcp add supabase -- npx -y @supabase/mcp-server-supabase \
  --access-token=$SUPABASE_ACCESS_TOKEN

# GitHub (опционально) — репозиторий
claude mcp add github -- npx -y @modelcontextprotocol/server-github
```

Правило (антипаттерн методички): каждый субагент **обязан** сверять API через Context7, чтобы не писать по устаревшим версиям aiogram/supabase-py.

---

## Файл `.env.example`

```bash
BOT_TOKEN=
WEBHOOK_URL=
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
GOOGLE_CREDENTIALS_JSON=./service_account.json
LOG_LEVEL=INFO
```

---

# СЛОЙ 5 — ФИНАЛЬНЫЙ ПРОМПТ СБОРКИ

> Скопируй это целиком в Claude Code / Cursor после того, как положил в репозиторий `PROJECT_IDEA.md`, `SPECIFICATION.md` и пакет конфигурации выше.

```
Ты — ведущий инженер проекта TG Traffic Analytics. В репозитории лежат:
PROJECT_IDEA.md, SPECIFICATION.md, CLAUDE.md, .claude/agents/*, .claude/rules/*,
.claude/skills/*, SPEC_TEMPLATE.md, .env.example.

ЗАДАЧА: реализовать MVP полностью, без TODO-заглушек, по SPECIFICATION.md.
Источник истины — SPECIFICATION.md. Не спрашивай уточнений, если ответ есть
в документах — добавляй сам по описанным правилам.

WORKFLOW (строго по слоям):
1. Прочитай PROJECT_IDEA.md и SPECIFICATION.md целиком.
2. Создай файловую структуру из CLAUDE.md (src/, supabase/migrations/, tests/).
3. database-architect: напиши миграции для accounts, sources, subscribers,
   events, customers, costs, settings — с типами, RLS, UNIQUE, индексами, FK.
4. backend-engineer: реализуй src/core (config, supabase client, logging) и
   src/services (attribution, analytics, sheets_sync, health) по модулям 2–6.
   Деления защити от нуля, атрибуцию сделай first-touch и идемпотентной.
5. bot-developer: реализуй src/bot (handlers chat_member/команды, keyboards,
   FSM онбординга) и src/main.py с set_webhook(allowed_updates=[...]).
6. Реализуй все команды из CLAUDE.md: /start /setchannel /setpaid /swap /status
   /newsource /sources /importlinks /cost /costs /stats.
7. Покрой крайние случаи из SPECIFICATION.md (нет прав, дубль join, нет источника,
   rate-limit, потеря прав бота, деление на ноль).
8. Напиши базовые тесты (pytest): атрибуция first-touch, идемпотентность,
   расчёт метрик с защитой от нуля.
9. qa-reviewer: проведи ревью по чеклисту (НЕ правь код, выдай список проблем).
10. Исправь все критичные и средние замечания → повтори ревью до чистого.

ОГРАНИЧЕНИЯ:
- aiogram 3.x (Router-архитектура), Python 3.11 async.
- Supabase под service_role; секреты только из .env.
- Google Sheets: не затирать колонки ввода «Расход»/«Цена продукта».
- Никаких TODO/заглушек/моков вместо логики.
- Перед использованием API aiogram/supabase-py/gspread сверяйся через Context7.

РЕЗУЛЬТАТ: рабочий бот, запускаемый `python -m src.main`, с применимыми
миграциями в supabase/migrations и проходящими тестами `pytest tests/`.
```

---

# Чеклист «Перед запуском» (Слой 5, методичка)

```
□ Репозиторий инициализирован (git init)
□ Supabase-проект создан, SUPABASE_URL и SERVICE_KEY получены
□ Google service account создан, service_account.json положен, лист расшарен на его email
□ .env заполнен (BOT_TOKEN, WEBHOOK_URL, SUPABASE_*, GOOGLE_CREDENTIALS_JSON)
□ Бот создан в @BotFather, токен получен
□ Бот добавлен админом в БЕСПЛАТНЫЙ канал (право can_invite_users)
□ Бот добавлен админом в ПЛАТНЫЙ чат
□ Claude Code установлен, MCP context7 + supabase подключены
□ Файловая структура из CLAUDE.md создана
□ PROJECT_IDEA.md и SPECIFICATION.md в репозитории
```

---