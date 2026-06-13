-- ============================================================
-- 004_customers_excluded.sql
-- Добавляем флаг excluded для ручного исключения продаж из учёта
-- Применить: Supabase Dashboard → SQL Editor → Run
-- ============================================================

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS excluded boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_customers_excluded
    ON customers(account_id, excluded);
