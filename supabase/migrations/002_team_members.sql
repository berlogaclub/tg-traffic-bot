-- ============================================================
-- 002_team_members.sql — командный доступ к проекту
-- Применить: Supabase Dashboard → SQL Editor → вставить и Run
-- ============================================================

-- Таблица участников команды (получают доступ к чужому аккаунту)
CREATE TABLE IF NOT EXISTS team_members (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    tg_user_id  bigint NOT NULL,
    added_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE(account_id, tg_user_id)
);

CREATE INDEX IF NOT EXISTS idx_team_members_tg_user_id ON team_members(tg_user_id);

-- RLS
ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_team_members" ON team_members
    USING (true) WITH CHECK (true);
