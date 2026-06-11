-- ============================================================
-- 003_fix_fk_cascade.sql
-- Исправляем FK: subscribers.source_id и customers.source_id
-- При удалении источника — source_id обнуляется (SET NULL),
-- сами записи подписчиков/клиентов сохраняются.
-- Применить: Supabase Dashboard → SQL Editor → Run
-- ============================================================

ALTER TABLE subscribers
    DROP CONSTRAINT IF EXISTS subscribers_source_id_fkey;
ALTER TABLE subscribers
    ADD CONSTRAINT subscribers_source_id_fkey
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL;

ALTER TABLE customers
    DROP CONSTRAINT IF EXISTS customers_source_id_fkey;
ALTER TABLE customers
    ADD CONSTRAINT customers_source_id_fkey
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL;
