-- =========================================================
-- Migration 004: /recommend/current 周缓存
-- =========================================================

-- 每个用户每个 iso 周同时最多一条
CREATE TABLE IF NOT EXISTS weekly_recommendations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
    plan_code       VARCHAR(40) NOT NULL,
    iso_year        INT NOT NULL,
    iso_week        INT NOT NULL,

    -- 直接存成最终可渲染的 JSON（与 API 返回结构一致，省二次拼装）
    payload         JSONB NOT NULL,
    total_cost_cny  NUMERIC(10, 2) NOT NULL,

    model           VARCHAR(40) NOT NULL,
    tokens_in       INT NOT NULL DEFAULT 0,
    tokens_out      INT NOT NULL DEFAULT 0,
    latency_ms      INT NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 同用户 + 同周唯一（保证缓存命中准确）
CREATE UNIQUE INDEX IF NOT EXISTS ux_weekly_user_week
    ON weekly_recommendations(user_id, iso_year, iso_week);

CREATE INDEX IF NOT EXISTS idx_weekly_user_time
    ON weekly_recommendations(user_id, created_at DESC);


-- LLM 调用日志（观测用，生产排障可查）
CREATE TABLE IF NOT EXISTS llm_call_logs (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        UUID NOT NULL,
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    purpose         VARCHAR(40) NOT NULL,          -- 'recommend'
    model           VARCHAR(40) NOT NULL,
    input_preview   TEXT,                          -- 入参前 1KB（脱敏）
    output_preview  TEXT,                          -- 出参前 1KB
    tokens_in       INT NOT NULL DEFAULT 0,
    tokens_out      INT NOT NULL DEFAULT 0,
    cached_tokens   INT NOT NULL DEFAULT 0,
    latency_ms      INT NOT NULL DEFAULT 0,
    stop_reason     VARCHAR(30),
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_llm_logs_user_time
    ON llm_call_logs(user_id, created_at DESC);
