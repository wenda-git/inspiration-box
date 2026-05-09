-- =========================================================
-- Migration 005: 水果主档 + 库存批次 + 箱型策略（后台可管理）
-- =========================================================

-- 水果主档（营养数据主要来源：中国食物成分表第 6 版，由后台人工录入）
CREATE TABLE IF NOT EXISTS fruits (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(60) UNIQUE NOT NULL,
    name_cn         VARCHAR(60) NOT NULL,
    name_en         VARCHAR(60),
    emoji           VARCHAR(10),
    category        VARCHAR(40),                 -- 浆果 / 柑橘 / 核果 / 仁果 / 瓜果 ...

    -- 每 100g 营养（按食物成分表可食部计）
    kcal            NUMERIC(6, 2) DEFAULT 0,
    carb_g          NUMERIC(6, 2) DEFAULT 0,
    sugar_g         NUMERIC(6, 2) DEFAULT 0,
    fiber_g         NUMERIC(6, 2) DEFAULT 0,
    protein_g       NUMERIC(6, 2) DEFAULT 0,
    fat_g           NUMERIC(6, 2) DEFAULT 0,
    gi              NUMERIC(5, 1),               -- 血糖指数（有的水果没 GI 数据，允许 null）
    vitC_mg         NUMERIC(7, 2) DEFAULT 0,
    folate_ug       NUMERIC(6, 1) DEFAULT 0,
    anthocyanin_mg  NUMERIC(7, 2) DEFAULT 0,
    potassium_mg    NUMERIC(7, 1) DEFAULT 0,
    calcium_mg      NUMERIC(6, 1) DEFAULT 0,
    iron_mg         NUMERIC(5, 2) DEFAULT 0,

    -- 营养来源引用
    source          VARCHAR(120),                -- e.g. "中国食物成分表第 6 版 · 表 5-1"
    source_note     TEXT,

    -- 方向适配度（主观运营参数）
    -- plan_affinity: {"fatloss_gi": 30, "prenatal": 0, "antiox": 40}
    plan_affinity   JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- 禁忌方向（一票否决）
    forbidden_for   TEXT[] NOT NULL DEFAULT '{}',
    -- 标签：['low_gi', 'high_vitC', 'high_anthocyanin']
    tags            TEXT[] NOT NULL DEFAULT '{}',

    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fruits_active ON fruits(is_active);
CREATE INDEX IF NOT EXISTS idx_fruits_tags ON fruits USING GIN(tags);


-- 库存批次（每周采购到货一条）
CREATE TABLE IF NOT EXISTS inventory_batches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fruit_code      VARCHAR(60) NOT NULL REFERENCES fruits(code) ON UPDATE CASCADE,
    batch_no        VARCHAR(60) UNIQUE NOT NULL,   -- 批次号，人工填，全局唯一

    warehouse_code  VARCHAR(40),                   -- 云仓代码
    qty_total_g     INT NOT NULL,
    qty_avail_g     INT NOT NULL,
    unit_cost_cny_per_kg NUMERIC(8, 2) NOT NULL,

    arrived_on      DATE NOT NULL DEFAULT current_date,
    best_before     DATE NOT NULL,

    -- 品控指标：糖度/甜度/损坏率/果径 等
    qc_metrics      JSONB NOT NULL DEFAULT '{}'::jsonb,

    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 选品时只查活跃 + 有余量的批次
CREATE INDEX IF NOT EXISTS idx_batches_available
    ON inventory_batches(fruit_code)
    WHERE is_active AND qty_avail_g > 0;
CREATE INDEX IF NOT EXISTS idx_batches_best_before
    ON inventory_batches(best_before)
    WHERE is_active;


-- 箱型策略（后台可编辑）
CREATE TABLE IF NOT EXISTS subscription_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(40) UNIQUE NOT NULL,
    name            VARCHAR(60) NOT NULL,
    tagline         VARCHAR(120),
    theme           VARCHAR(20) DEFAULT 'matcha',
    emoji           VARCHAR(10),

    price_cny       NUMERIC(8, 2) NOT NULL,
    budget_cny      NUMERIC(8, 2) NOT NULL,        -- 配单成本上限
    box_target_g    INT NOT NULL DEFAULT 2500,

    -- hard_rules: {"max_gi": 45, "max_sugar_g": 10, "min_fiber_g": 2.0, ...}
    hard_rules      JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- soft_prefs: {"max_items": 5, "min_items": 3, "single_share_max": 0.5}
    soft_prefs      JSONB NOT NULL DEFAULT '{}'::jsonb,

    sort_order      INT NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- 管理员账号（MVP 单账号，环境变量配置；本表留着以后扩）
CREATE TABLE IF NOT EXISTS admin_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(40) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,         -- bcrypt
    display_name    VARCHAR(60),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);


-- =========================================================
-- Seed 数据（把现有硬编码搬进 DB）
-- =========================================================

INSERT INTO subscription_plans
    (code, name, tagline, theme, emoji, price_cny, budget_cny, box_target_g, hard_rules, soft_prefs, sort_order)
VALUES
    ('fatloss_gi', '减脂控糖', '低 GI · 高纤维', 'matcha', '🥝',
     128, 128, 2500,
     '{"max_gi": 45, "max_sugar_g": 10, "min_fiber_g": 2.0}'::jsonb,
     '{"min_items": 2, "max_items": 5, "single_share_max": 0.5}'::jsonb,
     1),
    ('prenatal', '孕产营养', '高叶酸 · 高铁', 'berry', '🍇',
     168, 168, 2500,
     '{"min_folate_per_portion_ug": 40, "min_vitC_mg": 20}'::jsonb,
     '{"min_items": 2, "max_items": 5, "single_share_max": 0.5}'::jsonb,
     2),
    ('antiox', '抗氧轻食', '高花青素 · 高维 C', 'peach', '🍑',
     148, 148, 2500,
     '{"min_vitC_or_anthocyanin": true}'::jsonb,
     '{"min_items": 2, "max_items": 5, "single_share_max": 0.5}'::jsonb,
     3)
ON CONFLICT (code) DO NOTHING;


-- 初始水果（先录 8 种常见品类，其余由后台逐步补）
-- 数据源：中国食物成分表第 6 版，少数字段取 USDA 公开数据补充
INSERT INTO fruits
    (code, name_cn, emoji, category,
     kcal, carb_g, sugar_g, fiber_g, protein_g, fat_g, gi, vitC_mg, folate_ug, anthocyanin_mg, potassium_mg,
     source, plan_affinity, tags)
VALUES
    ('blueberry_yunnan', '云南蓝莓', '🫐', '浆果',
     57, 14.5, 9.9, 2.4, 0.7, 0.3, 53, 9.7, 6, 163, 77,
     '中国食物成分表第 6 版', '{"fatloss_gi": 30, "antiox": 40}'::jsonb, ARRAY['low_gi','high_anthocyanin']),

    ('grapefruit_red', '琯溪红心柚', '🍊', '柑橘',
     38, 9.6, 7.0, 1.6, 0.8, 0.1, 25, 38, 10, 0, 216,
     '中国食物成分表第 6 版', '{"fatloss_gi": 30}'::jsonb, ARRAY['low_gi','high_vitC']),

    ('apple_fuji', '烟台富士苹果', '🍎', '仁果',
     52, 13.8, 10.4, 2.4, 0.3, 0.2, 36, 4.6, 3, 0, 107,
     '中国食物成分表第 6 版', '{"fatloss_gi": 20}'::jsonb, ARRAY['low_gi','high_fiber']),

    ('kiwi_gold', '佳沛阳光金果', '🥝', '浆果',
     61, 15.8, 9.0, 3.0, 1.1, 0.5, 47, 105, 31, 0, 312,
     'USDA FDC 168153 近似', '{"prenatal": 35, "antiox": 30}'::jsonb, ARRAY['high_vitC','high_folate']),

    ('strawberry_danong', '丹东 99 草莓', '🍓', '浆果',
     32, 7.7, 4.9, 2.0, 0.7, 0.3, 40, 58, 24, 35, 153,
     '中国食物成分表第 6 版', '{"fatloss_gi": 25, "prenatal": 30, "antiox": 25}'::jsonb, ARRAY['low_sugar','high_vitC','high_folate']),

    ('pomegranate_tunisia', '突尼斯软籽石榴', '🍎', '仁果',
     83, 18.7, 14.0, 4.0, 1.7, 1.2, 35, 10, 38, 120, 236,
     'USDA FDC 169134 近似', '{"antiox": 35}'::jsonb, ARRAY['high_fiber','high_anthocyanin']),

    ('orange_gannan', '赣南脐橙', '🍊', '柑橘',
     47, 11.8, 9.4, 2.4, 0.9, 0.1, 43, 53, 30, 0, 181,
     '中国食物成分表第 6 版', '{"prenatal": 30, "antiox": 20}'::jsonb, ARRAY['high_vitC','high_folate']),

    ('cherry_yantai', '烟台大樱桃', '🍒', '核果',
     63, 16.0, 12.8, 2.1, 1.1, 0.2, 22, 7, 4, 55, 222,
     '中国食物成分表第 6 版', '{"prenatal": 20, "antiox": 30}'::jsonb, ARRAY['low_gi','high_anthocyanin'])

ON CONFLICT (code) DO NOTHING;


-- 初始库存：给上面每个水果开一个批次（模拟本周到货）
INSERT INTO inventory_batches
    (fruit_code, batch_no, warehouse_code, qty_total_g, qty_avail_g, unit_cost_cny_per_kg,
     arrived_on, best_before, qc_metrics)
VALUES
    ('blueberry_yunnan',   'BB-2619-A', 'WH-GZ', 50000, 50000, 80, current_date, current_date + 7,  '{"brix": 13.2, "damage_rate": 0.02}'),
    ('grapefruit_red',     'GF-2619-A', 'WH-GZ', 80000, 80000, 18, current_date, current_date + 14, '{"brix": 11.5}'),
    ('apple_fuji',         'AP-2619-A', 'WH-GZ', 120000, 120000, 14, current_date, current_date + 21, '{"brix": 14.0}'),
    ('kiwi_gold',          'KW-2619-A', 'WH-GZ', 60000, 60000, 42, current_date, current_date + 10, '{"brix": 15.8}'),
    ('strawberry_danong',  'SB-2619-A', 'WH-GZ', 40000, 40000, 60, current_date, current_date + 5,  '{"brix": 10.5}'),
    ('pomegranate_tunisia','PG-2619-A', 'WH-GZ', 50000, 50000, 36, current_date, current_date + 21, '{"brix": 16.0}'),
    ('orange_gannan',      'OR-2619-A', 'WH-GZ', 100000, 100000, 16, current_date, current_date + 14, '{"brix": 12.0}'),
    ('cherry_yantai',      'CH-2619-A', 'WH-GZ', 30000, 30000, 90, current_date, current_date + 5,  '{"brix": 18.0}')
ON CONFLICT (batch_no) DO NOTHING;
