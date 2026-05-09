-- =========================================================
-- Migration 010: 订阅支付 + 微信合同
-- =========================================================

-- 订阅表扩支付字段
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS wx_contract_id    VARCHAR(80),              -- 微信签约号
    ADD COLUMN IF NOT EXISTS billing_state     VARCHAR(20) NOT NULL DEFAULT 'unpaid',
    -- unpaid: 选了健康方向没付款 | paid: 首单已付正在订阅中 | cancelled: 已取消 | overdue: 续费扣款失败
    ADD COLUMN IF NOT EXISTS first_paid_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS first_payment_cny NUMERIC(10, 2),           -- 首单金额（折扣价）
    ADD COLUMN IF NOT EXISTS recurring_cny     NUMERIC(10, 2),            -- 后续每期金额
    ADD COLUMN IF NOT EXISTS next_charge_at    TIMESTAMPTZ,               -- 下一次扣费时间
    ADD COLUMN IF NOT EXISTS last_charge_at    TIMESTAMPTZ,               -- 上一次扣费时间
    ADD COLUMN IF NOT EXISTS charge_count      INT NOT NULL DEFAULT 0;    -- 已扣费多少次

CREATE INDEX IF NOT EXISTS idx_sub_next_charge
    ON subscriptions(next_charge_at)
    WHERE billing_state = 'paid' AND next_charge_at IS NOT NULL;


-- 支付流水：每一次真实收钱都留一条
CREATE TABLE IF NOT EXISTS payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id UUID REFERENCES subscriptions(id) ON DELETE SET NULL,

    kind            VARCHAR(20) NOT NULL,   -- 'first' 首单 | 'recurring' 续费 | 'refund' 退款
    amount_cny      NUMERIC(10, 2) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- pending: 发起支付等用户确认 | success | failed | cancelled | refunded

    idempotency_key VARCHAR(80) UNIQUE NOT NULL,    -- 我方订单号
    wx_prepay_id    VARCHAR(80),                    -- 微信下单返回的 prepay_id
    wx_tx_id        VARCHAR(80),                    -- 微信交易单号（支付成功后回填）
    wx_raw          JSONB,                          -- 回调原文

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at         TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payment_user_time
    ON payments(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payment_sub
    ON payments(subscription_id);
