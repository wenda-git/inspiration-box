-- =========================================================
-- Migration 003: 健康画像 + 订阅（MVP 精简版，无支付）
-- =========================================================

-- 健康画像（快照式，每次重大更新一条，当前最新用 is_current 标记）
CREATE TABLE IF NOT EXISTS health_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_current      BOOLEAN NOT NULL DEFAULT TRUE,

    gender          VARCHAR(10),                   -- 'male' | 'female' | 'other'
    height_cm       NUMERIC(5,2),
    weight_kg       NUMERIC(5,2),
    bmi             NUMERIC(4,2),                  -- 后端根据身高体重计算

    goals           TEXT[] NOT NULL DEFAULT '{}',  -- ['weight_loss','anti_aging',...]
    allergies       TEXT[] NOT NULL DEFAULT '{}',
    dislikes        TEXT[] NOT NULL DEFAULT '{}',
    tags            TEXT[] NOT NULL DEFAULT '{}',  -- AI 归纳的结构化标签（暂不用）
    raw_summary     TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每个用户同时最多一个 current 画像
CREATE UNIQUE INDEX IF NOT EXISTS ux_health_profile_current
    ON health_profiles(user_id) WHERE is_current;


-- 订阅（MVP：只记录用户选了哪个健康方向 + 绑定地址，不接支付）
CREATE TABLE IF NOT EXISTS subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_code       VARCHAR(40) NOT NULL,          -- 'fatloss_gi' | 'prenatal' | 'antiox'
    address_id      UUID REFERENCES addresses(id) ON DELETE SET NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'trial',
                     -- trial | active | paused | cancelled

    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每个用户同时最多一个 active/trial 订阅
CREATE UNIQUE INDEX IF NOT EXISTS ux_sub_user_active
    ON subscriptions(user_id)
    WHERE status IN ('trial', 'active', 'paused');

CREATE INDEX IF NOT EXISTS idx_sub_user_status
    ON subscriptions(user_id, status);
