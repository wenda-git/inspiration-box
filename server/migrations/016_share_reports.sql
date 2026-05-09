-- =========================================================
-- Migration 016: 分享报告短码
-- 支撑小程序分享页通过 sid 拉取轻量报告，避免把完整配单塞进 URL
-- =========================================================

CREATE TABLE IF NOT EXISTS share_reports (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    share_id    VARCHAR(24) NOT NULL UNIQUE,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    source      VARCHAR(30) NOT NULL DEFAULT 'weekly',
    payload     JSONB NOT NULL,
    view_count  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_share_reports_share_id
    ON share_reports(share_id);

CREATE INDEX IF NOT EXISTS idx_share_reports_created_at
    ON share_reports(created_at DESC);
