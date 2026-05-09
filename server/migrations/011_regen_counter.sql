-- =========================================================
-- Migration 011: weekly_recommendations 加"换一批"次数
-- =========================================================

ALTER TABLE weekly_recommendations
    ADD COLUMN IF NOT EXISTS regen_count INT NOT NULL DEFAULT 0;
