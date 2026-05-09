-- =========================================================
-- Migration 001: 初始化核心表
-- （对应早期设计的 schema v0.1 精简版，够当前链路跑）
-- =========================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- 用户
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_e164      VARCHAR(20),
    wx_openid       VARCHAR(64) UNIQUE,
    wx_unionid      VARCHAR(64) UNIQUE,
    nickname        VARCHAR(64),
    avatar_url      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
