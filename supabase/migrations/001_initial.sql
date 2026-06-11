-- ============================================================
-- 001_initial.sql — полная схема TG Traffic Analytics
-- Применить: Supabase Dashboard → SQL Editor → вставить и Run
-- ============================================================

-- ============================================================
-- ТАБЛИЦЫ
-- ============================================================

CREATE TABLE IF NOT EXISTS accounts (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tg_user_id       bigint UNIQUE NOT NULL,
    plan             text NOT NULL DEFAULT 'free',
    free_channel_id  bigint,
    paid_chat_id     bigint,
    product_price    numeric(12,2) DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sources (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name         text NOT NULL,
    invite_link  text NOT NULL,
    invite_name  text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE(account_id, name)
);

CREATE TABLE IF NOT EXISTS subscribers (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    tg_user_id          bigint NOT NULL,
    source_id           uuid REFERENCES sources(id),
    username            text,
    full_name           text,
    joined_at           timestamptz NOT NULL DEFAULT now(),
    attribution_locked  boolean NOT NULL DEFAULT true,
    UNIQUE(account_id, tg_user_id)
);

CREATE TABLE IF NOT EXISTS events (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    tg_user_id   bigint NOT NULL,
    chat_kind    text NOT NULL,
    event_type   text NOT NULL,
    invite_name  text,
    raw          jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customers (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id     uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    tg_user_id     bigint NOT NULL,
    source_id      uuid REFERENCES sources(id),
    subscriber_id  uuid REFERENCES subscribers(id),
    entry_type     text NOT NULL DEFAULT 'paid',
    amount         numeric(12,2),
    bought_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE(account_id, tg_user_id)
);

CREATE TABLE IF NOT EXISTS costs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source_id    uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    amount       numeric(12,2) NOT NULL,
    period_start date,
    period_end   date,
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS settings (
    account_id        uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    sheet_id          text,
    sync_enabled      boolean NOT NULL DEFAULT false,
    sync_interval_min int NOT NULL DEFAULT 60,
    last_synced_at    timestamptz
);

-- ============================================================
-- ИНДЕКСЫ
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_subscribers_account_user ON subscribers(account_id, tg_user_id);
CREATE INDEX IF NOT EXISTS idx_customers_account_user   ON customers(account_id, tg_user_id);
CREATE INDEX IF NOT EXISTS idx_events_account_created   ON events(account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_costs_source             ON costs(source_id);
CREATE INDEX IF NOT EXISTS idx_sources_account          ON sources(account_id);
CREATE INDEX IF NOT EXISTS idx_accounts_free_channel    ON accounts(free_channel_id);
CREATE INDEX IF NOT EXISTS idx_accounts_paid_chat       ON accounts(paid_chat_id);

-- ============================================================
-- RLS (Row Level Security)
-- service_role обходит RLS — бот работает под service_role
-- ============================================================

ALTER TABLE accounts    ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources     ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscribers ENABLE ROW LEVEL SECURITY;
ALTER TABLE events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers   ENABLE ROW LEVEL SECURITY;
ALTER TABLE costs       ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings    ENABLE ROW LEVEL SECURITY;
