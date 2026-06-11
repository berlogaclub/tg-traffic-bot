Формат: 6 блоков на модуль (User Stories / Модель данных / API и серверная логика / Экраны и компоненты / Бизнес-логика / Крайние случаи). Типы — как в SQL. Для бота «API» = команды/хендлеры aiogram и Server-функции.

Глобальная модель данных
sql

-- accounts: владельцы проектов
accounts:
  id            uuid PK DEFAULT gen_random_uuid()
  tg_user_id    bigint UNIQUE NOT NULL        -- владелец-админ
  plan          text NOT NULL DEFAULT 'free'  -- free | pro | agency
  free_channel_id  bigint                     -- id бесплатного канала
  paid_chat_id     bigint                     -- id платного чата
  product_price    numeric(12,2) DEFAULT 0    -- цена продукта (₽)
  created_at    timestamptz NOT NULL DEFAULT now()

-- sources: источники трафика = именованные invite-ссылки
sources:
  id              uuid PK DEFAULT gen_random_uuid()
  account_id      uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE
  name            text NOT NULL                -- "YouTube_video_3"
  invite_link     text NOT NULL                -- t.me/+xxxx
  invite_name     text NOT NULL                -- name ссылки в Telegram
  created_at      timestamptz NOT NULL DEFAULT now()
  UNIQUE(account_id, name)

-- subscribers: подписчики бесплатного канала с атрибуцией
subscribers:
  id              uuid PK DEFAULT gen_random_uuid()
  account_id      uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE
  tg_user_id      bigint NOT NULL
  source_id       uuid REFERENCES sources(id)  -- NULL = источник не определён
  username        text
  full_name       text
  joined_at       timestamptz NOT NULL DEFAULT now()
  attribution_locked boolean NOT NULL DEFAULT true  -- первая атрибуция зафиксирована
  UNIQUE(account_id, tg_user_id)

-- events: лог всех событий chat_member (аудит)
events:
  id              uuid PK DEFAULT gen_random_uuid()
  account_id      uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE
  tg_user_id      bigint NOT NULL
  chat_kind       text NOT NULL                -- 'free' | 'paid'
  event_type      text NOT NULL                -- 'join' | 'leave'
  invite_name     text                         -- если есть
  raw             jsonb                         -- сырой апдейт
  created_at      timestamptz NOT NULL DEFAULT now()

-- customers: клиенты (вступили в платный чат)
customers:
  id              uuid PK DEFAULT gen_random_uuid()
  account_id      uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE
  tg_user_id      bigint NOT NULL
  source_id       uuid REFERENCES sources(id)  -- унаследован от subscribers
  subscriber_id   uuid REFERENCES subscribers(id)
  entry_type      text NOT NULL DEFAULT 'paid' -- 'paid' | 'manual'
  amount          numeric(12,2)                -- сумма (если известна)
  bought_at       timestamptz NOT NULL DEFAULT now()
  UNIQUE(account_id, tg_user_id)

-- costs: расходы по источникам
costs:
  id              uuid PK DEFAULT gen_random_uuid()
  account_id      uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE
  source_id       uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE
  amount          numeric(12,2) NOT NULL
  period_start    date
  period_end      date
  note            text
  created_at      timestamptz NOT NULL DEFAULT now()

-- settings: настройки синка
settings:
  account_id          uuid PK REFERENCES accounts(id) ON DELETE CASCADE
  sheet_id            text          -- Google Sheet ID
  sync_enabled        boolean NOT NULL DEFAULT false
  sync_interval_min   int NOT NULL DEFAULT 60
  last_synced_at      timestamptz
RLS-политики (все таблицы)
Бот ходит в БД под service_role (серверный бэкенд, не клиент). RLS включается для защиты при будущем веб/API-доступе:

sql

-- accounts
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
SELECT/UPDATE: auth.uid()::text = tg_user_id::text   -- при подключении Supabase Auth (v3)

-- дочерние таблицы (sources, subscribers, events, customers, costs, settings)
ENABLE ROW LEVEL SECURITY;
SELECT/INSERT/UPDATE/DELETE:
  account_id IN (SELECT id FROM accounts WHERE tg_user_id::text = auth.uid()::text)

-- service_role обходит RLS (используется ботом) — это штатный режим Supabase
Индексы:

sql

CREATE INDEX idx_subscribers_account_user ON subscribers(account_id, tg_user_id);
CREATE INDEX idx_customers_account_user   ON customers(account_id, tg_user_id);
CREATE INDEX idx_events_account_created   ON events(account_id, created_at);
CREATE INDEX idx_costs_source             ON costs(source_id);
Модуль 1 — source-manager (источники и invite-ссылки)
User Stories

Как владелец, я хочу создать новый источник с именем, чтобы получить под него персональную invite-ссылку.
Как владелец, я хочу видеть список всех источников и их ссылки, чтобы раздавать их в рекламу.
Как владелец, я хочу импортировать уже существующие именованные ссылки канала, чтобы не пересоздавать их.
(Edge) Как владелец, я хочу получить понятную ошибку при дубликате имени источника.
(Edge) Как владелец, я хочу запрет на создание ссылки, если бот не админ канала.
API / Серверная логика (aiogram)

Команда /newsource <имя> → создаёт invite-ссылку через createChatInviteLink(chat_id=free_channel_id, name=<имя>, creates_join_request=false) → INSERT в sources → ответ со ссылкой. Ошибки: имя занято (409-аналог) → текст «Источник с таким именем уже есть»; бот не админ → «Бот не админ канала».
Команда /sources → SELECT из sources → список «имя — ссылка».
Команда /importlinks → getChatInviteLink-список (через хранимые/вручную) → upsert в sources.
Server-функция create_source(account_id, name) -> Source.
Экраны и компоненты

Сообщение-список источников (inline-кнопки: «Скопировать», «Статистика», «Удалить»).
Состояния: загрузка («Создаю ссылку…»), успех (ссылка), пусто («Источников нет, создай /newsource»), ошибка (текст причины).
Бизнес-логика

name уникален в рамках account_id.
invite_name = name (Telegram хранит имя ссылки до 32 символов → валидация длины).
Перед созданием — проверка getChatMember(free_channel_id, bot_id) = administrator с правом can_invite_users.
Крайние случаи

Имя длиннее 32 символов → обрезать/ошибка с подсказкой.
Telegram вернул rate-limit → retry с backoff, сообщение «Попробуй через минуту».
Канал не задан в accounts.free_channel_id → запрос на привязку канала (см. Модуль 6).
Модуль 2 — subscriber-tracker (атрибуция подписки)
User Stories

Как система, я хочу при вступлении в бесплатный канал определить источник по invite_link.name и сохранить подписчика, чтобы знать происхождение.
Как система, я хочу фиксировать первую атрибуцию и не перезаписывать её при повторном входе.
Как владелец, я хочу видеть подписчиков без источника отдельной группой «не определён».
(Edge) вступление без invite_link (по публичной ссылке/добавлен админом) → source_id = NULL.
(Edge) повторное вступление того же user_id → не дублировать, атрибуция не меняется.
API / Серверная логика

Хендлер chat_member (фильтр: chat.id == free_channel_id).
Условие join: old_chat_member.status in ('left','kicked') И new_chat_member.status in ('member','restricted').
Извлечь invite_link.name из апдейта → найти source_id по invite_name.
UPSERT в subscribers (по account_id, tg_user_id): при вставке записать source_id, attribution_locked=true. При конфликте — НЕ менять source_id, если attribution_locked=true.
INSERT в events (chat_kind='free', event_type='join', raw=апдейт).
Server-функция attribute_subscriber(account_id, tg_user_id, invite_name) -> Subscriber.
Экраны и компоненты

Нет UI; работает в фоне. Опционально — уведомление владельцу «+1 подписчик из <источник>» (настраивается).
Бизнес-логика

Атрибуция = first-touch (первое join-событие).
Если invite_name не найден в sources (старая/удалённая ссылка) → создать «теневой» источник или source_id=NULL (решение: NULL + лог).
Крайние случаи

allowed_updates не содержит chat_member → апдейты не придут (см. Модуль 6: проверка при старте).
Бот добавлен админом задним числом → старые подписчики не атрибутируются (документировано, fallback NULL).
Спам массовых вступлений → батч-обработка, защита от дублей по UNIQUE.
Модуль 3 — customer-tracker (атрибуция продажи)
User Stories

Как система, я хочу при вступлении в платный чат найти user_id среди подписчиков и связать продажу с его источником.
Как владелец, я хочу видеть клиента даже если он не был найден среди подписчиков (источник «не определён»), чтобы не терять факт продажи.
Как владелец, я хочу различать оплативших и добавленных вручную, чтобы метрики не искажались.
(Edge) вступление в платный чат пользователя, которого нет в subscribers → создать customer с source_id=NULL, entry_type='manual'.
(Edge) повторное вступление того же клиента → не дублировать.
API / Серверная логика

Хендлер chat_member (фильтр: chat.id == paid_chat_id).
Условие join: old_status in ('left','kicked') И new_status in ('member','restricted').
Найти subscribers по account_id, tg_user_id.
Если найден → source_id = subscriber.source_id, subscriber_id = subscriber.id, entry_type='paid'.
Если не найден → source_id=NULL, subscriber_id=NULL, entry_type='manual'.
amount = accounts.product_price (если задана), иначе NULL.
UPSERT в customers (по account_id, tg_user_id).
INSERT в events (chat_kind='paid', event_type='join').
Server-функция attribute_customer(account_id, tg_user_id) -> Customer.
Экраны и компоненты

Опциональное уведомление владельцу: «💰 Новая продажа! Источник: <название> | Сумма: <amount>».
Состояния уведомления: источник определён / не определён.
Бизнес-логика

entry_type='paid' ставится только когда найден подписчик ИЛИ владелец включил режим «все вступления в платный чат = покупка».
Ручные добавления (entry_type='manual') исключаются из расчёта CAC по умолчанию (настраивается).
Сумма берётся из product_price; при разных тарифах — переопределяется через Sheets (Модуль 5).
Крайние случаи

Клиент вышел из платного чата → event_type='leave', запись customers сохраняется (отток считается отдельно).
Клиент вернулся → не дублировать, bought_at не меняется.
Платный чат не привязан (paid_chat_id IS NULL) → лог + алерт владельцу.
Модуль 4 — cost-manager (расходы по источникам)
User Stories

Как владелец, я хочу внести расход на источник за период, чтобы система посчитала окупаемость.
Как владелец, я хочу редактировать/удалять расход, чтобы исправлять ошибки.
Как владелец, я хочу вносить расходы в Google Sheets, чтобы не делать это командами.
(Edge) расход на несуществующий источник → ошибка с подсказкой.
(Edge) отрицательная или нечисловая сумма → валидация.
API / Серверная логика

Команда /cost <источник> <сумма> [период] → проверка источника → INSERT в costs. Ошибки: источник не найден → «Нет такого источника, см. /sources»; сумма ≤ 0 → «Сумма должна быть положительной».
Команда /costs → SELECT по источникам → сводка расходов.
Server-функция add_cost(account_id, source_id, amount, period_start, period_end) -> Cost.
Импорт из Sheets — см. Модуль 5 (двусторонний синк).
Экраны и компоненты

Сообщение-сводка расходов по источникам (inline: «Изменить», «Удалить», «Добавить»).
Состояния: пусто («Расходы не внесены»), успех, ошибка валидации.
Бизнес-логика

Расход агрегируется по source_id (сумма всех записей costs источника, опц. в пределах периода).
amount — numeric(12,2), валидация > 0.
Период необязателен (для общего расхода без дат).
Крайние случаи

Дубль расхода за один период → разрешён (несколько закупок), агрегируется суммой.
Удаление источника → каскадно удаляет его costs (FK ON DELETE CASCADE).
Модуль 5 — analytics-engine + sheets-sync (метрики и синхронизация)
User Stories

Как владелец, я хочу видеть по каждому источнику: подписчики, клиенты, конверсию, CPF, CAC, выручку, ROMI, окупаемость.
Как владелец, я хочу авто-обновление Google Sheets раз в час, чтобы данные были свежими.
Как владелец, я хочу вводить расход и цену продукта прямо в Sheets, а метрики получать обратно.
(Edge) деление на ноль (0 подписчиков/клиентов/расхода) → метрика = —, не падать.
(Edge) Sheets недоступен / rate-limit → retry с backoff, не терять данные.
API / Серверная логика

Server-функция compute_metrics(account_id) -> list[SourceMetrics]:
Для каждого source:
subscribers_count = COUNT(subscribers WHERE source_id=...)
customers_count = COUNT(customers WHERE source_id=... AND entry_type='paid')
cost = SUM(costs.amount WHERE source_id=...)
revenue = customers_count × accounts.product_price (или Σ customers.amount)
conversion = customers_count / subscribers_count
cpf = cost / subscribers_count
cac = cost / customers_count
romi = (revenue − cost) / cost × 100%
payback = revenue / cost
Отдельная строка «Источник не определён» (source_id IS NULL).
Server-функция sync_to_sheets(account_id):
Чтение из Sheets: колонки Расход, Цена продукта → upsert в costs / accounts.product_price.
Запись в Sheets: рассчитанные метрики по источникам.
Батч-операции через gspread batch_update, backoff при 429.
Cron (APScheduler): compute_metrics + sync_to_sheets каждые settings.sync_interval_min минут.
Экраны и компоненты

Команда /stats → таблица-сообщение по источникам (моноширинный формат).
Команда /stats <источник> → детализация одного источника.
Google Sheet: лист «Sources» — строки источников, колонки метрик (см. макет ниже).
Состояния: загрузка («Считаю метрики…»), успех (таблица), нет данных, ошибка синка.
Макет Google Sheet (лист «Sources»)

Источник	Подписчики	Клиенты	Конв.%	Расход (ввод)	Цена продукта (ввод)	Выручка	CPF	CAC	ROMI%	Окуп.
YouTube_video_3	1200	48	4.0	60000	2990	143520	50	1250	139	2.39
Посев_канал_X	800	12	1.5	50000	2990	35880	62.5	4167	−28	0.72
Источник не определён	340	9	2.6	0	—	—	—	—	—	—
Колонки «Расход (ввод)» и «Цена продукта (ввод)» — редактируются владельцем, остальное пишет бот.

Бизнес-логика

Все деления защищены: знаменатель 0 → —.
ROMI считается только при cost > 0.
Источники сортируются по revenue DESC (или romi DESC — настраивается).
Ручные клиенты (entry_type='manual') считаются в выручке, но не в CAC.
Крайние случаи

Конфликт записи Sheets (владелец редактирует во время синка) → читать перед записью, не затирать колонки ввода.
Цена продукта изменилась в середине периода → новые продажи по новой цене (хранить customers.amount на момент покупки).
Лист удалён/переименован → создать заново по шаблону, лог.
Модуль 6 — setup & health (привязка каналов, проверки)
User Stories

Как владелец, я хочу привязать бесплатный канал и платный чат, чтобы бот начал работу.
Как владелец, я хочу получить инструкцию по выдаче боту прав админа.
Как система, я хочу при старте проверить allowed_updates и права бота, чтобы не молчать при сбое.
(Edge) бот потерял права админа → алерт владельцу.
API / Серверная логика

Команда /start → онбординг: создать accounts (по tg_user_id), инструкция.
Команда /setchannel (переслать сообщение из канала / ввести id) → сохранить free_channel_id, проверить права.
Команда /setpaid → сохранить paid_chat_id, проверить права.
Startup-хук: set_webhook(allowed_updates=["message","callback_query","chat_member","chat_join_request","my_chat_member"]).
my_chat_member-хендлер → отслеживает изменение статуса самого бота (повышен/понижен в админах).
Health-check (cron): getChatMember(chat, bot_id) для обоих чатов → при потере прав алерт.
Экраны и компоненты

Онбординг-сообщения с кнопками «Привязать канал», «Привязать чат», «Готово».
Статус-сообщение /status: каналы привязаны? права есть? синк включён? последний синк?
Бизнес-логика

Один account на одного владельца в MVP (мультипроект — v3).
Webhook предпочтительнее polling для продакшна (но polling допустим на старте).
Крайние случаи

Владелец привязал не тот чат (перепутал free/paid) → команда /swap или повторная привязка.
Бот не имеет права can_invite_users → блокировать /newsource с подсказкой.
Бизнес-логика: ключевые формулы (сводно)

conversion = customers_count / subscribers_count            (0 → «—»)
CPF        = cost / subscribers_count                       (0 → «—»)
CAC        = cost / customers_count                         (0 → «—»)
revenue    = Σ customers.amount  ИЛИ  customers_count × product_price
ROMI%      = (revenue − cost) / cost × 100                  (cost=0 → «—»)
payback    = revenue / cost                                 (cost=0 → «—»)
Глобальные крайние случаи
Источник недоступен задним числом — первичная атрибуция только в момент join; fallback source_id=NULL.
first-touch фиксируется (attribution_locked) — повторные входы не меняют источник.
Нет chat_member апдейтов — явный allowed_updates, health-check при старте.
Rate-limit Telegram/Sheets — экспоненциальный backoff, очередь.
Race condition (одновременный join в free и paid) — обработка идемпотентна по UNIQUE-ключам.
Чеклист готовности спецификации (по методичке)
Документ идеи

☑ Проблема с числами (20–40% бюджета, 100–500к ₽/мес)
☑ Решение — пошаговый процесс (5 шагов → модули)
☑ Архитектура с диаграммой слоёв
☑ Стек с обоснованием
☑ Монетизация с планами и ценами
☑ Конкуренты в таблице
☑ MVP выделен отдельно от v2/v3
Спецификация

☑ Каждый модуль: user stories + данные + API + UI + логика + edge cases
☑ Типы полей как в SQL (uuid, bigint, numeric, timestamptz, jsonb)
☑ API: метод/команда + вход + ответ + коды/ошибки
☑ RLS для каждой таблицы
☑ AI-агентов в рантайме нет → ai-agent-architect не нужен (классификатор пройден)
☑ Нет TODO-заглушек