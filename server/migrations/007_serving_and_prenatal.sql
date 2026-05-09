-- =========================================================
-- Migration 007: 按件/盒分量 + 孕产三阶段
-- =========================================================

-- 1. fruits 加"默认规格"字段：单份克数 + 单位
ALTER TABLE fruits
    ADD COLUMN IF NOT EXISTS serving_unit   VARCHAR(20) DEFAULT 'g',      -- 'piece'|'box'|'half_kg'|'g'
    ADD COLUMN IF NOT EXISTS serving_size_g INT         DEFAULT 500;      -- 1 份多重

-- 回填常见水果的真实规格
UPDATE fruits SET serving_unit = 'piece', serving_size_g = 200 WHERE code IN ('apple_fuji','grapefruit_red','orange_gannan','pomegranate_tunisia','avocado_hass');
UPDATE fruits SET serving_unit = 'piece', serving_size_g = 150 WHERE code = 'kiwi_gold';
UPDATE fruits SET serving_unit = 'piece', serving_size_g = 300 WHERE code = 'dragonfruit_red';
UPDATE fruits SET serving_unit = 'piece', serving_size_g = 50  WHERE code = 'passionfruit';
UPDATE fruits SET serving_unit = 'box',   serving_size_g = 125 WHERE code = 'blueberry_yunnan';
UPDATE fruits SET serving_unit = 'box',   serving_size_g = 250 WHERE code = 'strawberry_danong';
UPDATE fruits SET serving_unit = 'box',   serving_size_g = 500 WHERE code = 'cherry_yantai';
UPDATE fruits SET serving_unit = 'g',     serving_size_g = 250 WHERE code = 'guava_taiwan';


-- 2. health_profiles 加孕期阶段（孕产方向专用）
ALTER TABLE health_profiles
    ADD COLUMN IF NOT EXISTS prenatal_stage VARCHAR(20);   -- 'trying'|'first_tri'|'second_tri'|'third_tri'|'postpartum'|null


-- 3. 拆分 prenatal 方向：按 stage 不同的硬约束
-- 数据层仍存在同一行 subscription_plans (prenatal)，但硬约束里多一个 "by_stage" 字段
UPDATE subscription_plans
SET hard_rules = jsonb_build_object(
    'default', jsonb_build_object('min_folate_per_portion_ug', 40, 'min_vitC_mg', 20),
    'by_stage', jsonb_build_object(
        'first_tri',  jsonb_build_object('min_folate_per_portion_ug', 60, 'min_vitC_mg', 25, 'max_gi', 55),
        'second_tri', jsonb_build_object('min_folate_per_portion_ug', 40, 'min_vitC_mg', 25, 'min_iron_mg', 0.3, 'max_gi', 50),
        'third_tri',  jsonb_build_object('min_folate_per_portion_ug', 30, 'min_vitC_mg', 25, 'max_sugar_g', 10, 'max_gi', 45, 'min_fiber_g', 2.5)
    )
)
WHERE code = 'prenatal';
