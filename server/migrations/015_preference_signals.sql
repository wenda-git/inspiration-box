-- =========================================================
-- Migration 015: 用户偏好信号
-- 把反馈沉淀成结构化权重，供配单、个人偏好档案和运营分析复用。
-- 不用 enum，避免后续新增信号类型时频繁迁移。
-- =========================================================

CREATE TABLE IF NOT EXISTS user_preference_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    signal_type     VARCHAR(40) NOT NULL,   -- fruit | taste | freshness | amount
    signal_key      VARCHAR(80) NOT NULL,   -- fruit_code / sweet / sour / sensitive / more / less
    weight          NUMERIC(8,2) NOT NULL DEFAULT 0,
    source_count    INTEGER NOT NULL DEFAULT 0,
    last_feedback_id UUID,
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, signal_type, signal_key)
);

CREATE INDEX IF NOT EXISTS idx_preference_signals_user_type
    ON user_preference_signals(user_id, signal_type, weight DESC);
