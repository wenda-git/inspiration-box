-- =========================================================
-- Migration 002: Auth + Addresses
-- =========================================================

-- users 表已有 phone_e164 字段，这里补唯一索引（未删除的用户内唯一）
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_phone_active
    ON users(phone_e164)
    WHERE phone_e164 IS NOT NULL AND deleted_at IS NULL;


-- 短信验证码临时表（不做账号历史，定时清理）
CREATE TABLE IF NOT EXISTS sms_codes (
    id              BIGSERIAL PRIMARY KEY,
    phone_e164      VARCHAR(20) NOT NULL,
    code_hash       VARCHAR(64) NOT NULL,          -- sha256(code + salt)，不存明文
    purpose         VARCHAR(20) NOT NULL DEFAULT 'login', -- login | bind | reset
    attempts        INT NOT NULL DEFAULT 0,        -- 校验失败次数，>5 作废
    consumed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL            -- 一般 5 分钟
);
CREATE INDEX IF NOT EXISTS idx_sms_phone_time
    ON sms_codes(phone_e164, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sms_expires
    ON sms_codes(expires_at)
    WHERE consumed_at IS NULL;


-- 收货地址
CREATE TABLE IF NOT EXISTS addresses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_name   VARCHAR(40) NOT NULL,
    receiver_phone  VARCHAR(20) NOT NULL,
    region          VARCHAR(80) NOT NULL,          -- "广东省/广州市/越秀区"
    detail          VARCHAR(200) NOT NULL,         -- 详细地址
    is_default      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_addr_user
    ON addresses(user_id)
    WHERE deleted_at IS NULL;

-- 每个用户最多一个默认地址（软删除的不算）
CREATE UNIQUE INDEX IF NOT EXISTS ux_addr_user_default
    ON addresses(user_id)
    WHERE is_default = TRUE AND deleted_at IS NULL;
