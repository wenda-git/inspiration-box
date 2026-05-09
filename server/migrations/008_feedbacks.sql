-- =========================================================
-- Migration 008: feedbacks 表（MVP 版）
-- =========================================================

CREATE TYPE feedback_kind_t AS ENUM ('taste','freshness','damage','allergy','preference','other');

CREATE TABLE IF NOT EXISTS feedbacks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id        UUID,
    fruit_id        VARCHAR(60),                       -- 直接存 fruit_code
    recommendation_id UUID,
    kind            feedback_kind_t NOT NULL DEFAULT 'taste',
    rating          SMALLINT,                          -- 1-5
    comment         TEXT,
    photo_urls      TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_user_time
    ON feedbacks(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_user_negative
    ON feedbacks(user_id)
    WHERE rating IS NOT NULL AND rating <= 2;
