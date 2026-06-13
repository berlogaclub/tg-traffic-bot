-- ============================================================
-- 005_sources_join_request.sql
-- Тип ссылки: false = прямое вступление, true = заявка на вступление
-- Применить: Supabase Dashboard → SQL Editor → Run
-- ============================================================

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS join_request boolean NOT NULL DEFAULT false;
