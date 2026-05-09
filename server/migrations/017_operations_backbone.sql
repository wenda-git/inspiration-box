-- =========================================================
-- Migration 017: 经营后台骨架
-- 库存预占、订阅暂停、订单履约备注
-- =========================================================

CREATE TABLE IF NOT EXISTS order_inventory_reservations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id    UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    batch_no    VARCHAR(60) NOT NULL REFERENCES inventory_batches(batch_no) ON UPDATE CASCADE,
    fruit_code  VARCHAR(60) NOT NULL REFERENCES fruits(code) ON UPDATE CASCADE,
    qty_g       INT NOT NULL CHECK (qty_g > 0),
    status      VARCHAR(20) NOT NULL DEFAULT 'reserved',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(order_id, batch_no, fruit_code)
);

CREATE INDEX IF NOT EXISTS idx_order_reservations_order
    ON order_inventory_reservations(order_id);

CREATE INDEX IF NOT EXISTS idx_order_reservations_status
    ON order_inventory_reservations(status);

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pause_until DATE,
    ADD COLUMN IF NOT EXISTS pause_reason TEXT;

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS admin_note TEXT,
    ADD COLUMN IF NOT EXISTS auto_locked BOOLEAN NOT NULL DEFAULT FALSE;

-- 兼容旧逻辑：历史订单曾在生成配单时直接扣了 qty_avail_g。
-- 这里仅补 reservation 记录，不再二次扣库存；取消旧锁单时可正确释放。
INSERT INTO order_inventory_reservations
    (order_id, batch_no, fruit_code, qty_g, status)
SELECT
    o.id,
    item->>'batch_id',
    item->>'fruit_code',
    GREATEST((item->>'qty_g')::int, 1),
    CASE
      WHEN o.status IN ('shipped', 'delivered') THEN 'deducted'
      WHEN o.status = 'cancelled' THEN 'released'
      ELSE 'reserved'
    END
  FROM orders o
 CROSS JOIN LATERAL jsonb_array_elements(o.items_snapshot) AS item
  JOIN inventory_batches b ON b.batch_no = item->>'batch_id'
 WHERE item ? 'batch_id'
   AND item ? 'fruit_code'
   AND item ? 'qty_g'
ON CONFLICT (order_id, batch_no, fruit_code) DO NOTHING;
