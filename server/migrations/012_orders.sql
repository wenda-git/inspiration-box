-- =========================================================
-- Migration 012: 订单 + 锁单确认 + 订单水果明细
-- 支撑"付款 → 锁单 → 发货 → 签收 → 反馈"状态机
-- =========================================================

-- 订单：每周一期
CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
    recommendation_id UUID REFERENCES weekly_recommendations(id) ON DELETE SET NULL,
    payment_id      UUID REFERENCES payments(id) ON DELETE SET NULL,
    address_id      UUID REFERENCES addresses(id) ON DELETE SET NULL,

    iso_year        INT NOT NULL,
    iso_week        INT NOT NULL,
    plan_code       VARCHAR(40) NOT NULL,

    -- 状态机：
    --   locked     用户付款 + 本周已锁单（不能再换一批）
    --   packing    云仓分拣中（周四截单后）
    --   shipped    已发出，tracking_no 已回填
    --   delivered  已签收
    --   cancelled  售后取消
    status          VARCHAR(20) NOT NULL DEFAULT 'locked',

    items_snapshot  JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_cost_cny  NUMERIC(10, 2) NOT NULL DEFAULT 0,

    -- 物流
    carrier_code    VARCHAR(20),           -- 'sf_cold' 等
    tracking_no     VARCHAR(60),
    shipped_at      TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,

    -- 用户主动确认锁单的时间（"确认配单"按钮）
    locked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每个用户每周最多一单
CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_user_week
    ON orders(user_id, iso_year, iso_week);

CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_user_time
    ON orders(user_id, created_at DESC);
