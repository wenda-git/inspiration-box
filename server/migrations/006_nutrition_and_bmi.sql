-- =========================================================
-- Migration 006: 营养维度扩充 + BMI 分档
-- =========================================================

-- 1. fruits 表加镁、ORAC（抗氧化能力）、果糖（痛风相关）
ALTER TABLE fruits
    ADD COLUMN IF NOT EXISTS magnesium_mg    NUMERIC(6, 1) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS fructose_g      NUMERIC(5, 2) DEFAULT 0,   -- 果糖，痛风参考
    ADD COLUMN IF NOT EXISTS orac_umol_te    NUMERIC(7, 0) DEFAULT 0;   -- ORAC（μmol TE/100g）

-- 2. health_profiles 加 BMI 分档派生字段（视图用 + 选品快速查）
ALTER TABLE health_profiles
    ADD COLUMN IF NOT EXISTS bmi_band VARCHAR(20);

-- 根据现有 bmi 回填 band
UPDATE health_profiles SET bmi_band =
    CASE
        WHEN bmi < 18.5 THEN 'underweight'
        WHEN bmi < 24.0 THEN 'normal'
        WHEN bmi < 28.0 THEN 'overweight'
        ELSE 'obese'
    END
    WHERE bmi IS NOT NULL AND bmi_band IS NULL;


-- 3. 补齐 seed 水果的完整营养数据（镁、ORAC、钙、铁、果糖）
-- 来源：中国食物成分表第 6 版 + USDA FDC（ORAC 来源 USDA 2010 ORAC 表）

UPDATE fruits SET
    magnesium_mg = 6,   calcium_mg = 8,   iron_mg = 0.28, fructose_g = 4.97, orac_umol_te = 4669
    WHERE code = 'blueberry_yunnan';

UPDATE fruits SET
    magnesium_mg = 9,   calcium_mg = 22,  iron_mg = 0.08, fructose_g = 3.61, orac_umol_te = 1548
    WHERE code = 'grapefruit_red';

UPDATE fruits SET
    magnesium_mg = 5,   calcium_mg = 6,   iron_mg = 0.12, fructose_g = 5.90, orac_umol_te = 3049
    WHERE code = 'apple_fuji';

UPDATE fruits SET
    magnesium_mg = 17,  calcium_mg = 34,  iron_mg = 0.31, fructose_g = 4.35, orac_umol_te = 1210
    WHERE code = 'kiwi_gold';

UPDATE fruits SET
    magnesium_mg = 13,  calcium_mg = 16,  iron_mg = 0.41, fructose_g = 2.44, orac_umol_te = 3577
    WHERE code = 'strawberry_danong';

UPDATE fruits SET
    magnesium_mg = 12,  calcium_mg = 10,  iron_mg = 0.30, fructose_g = 6.40, orac_umol_te = 4479
    WHERE code = 'pomegranate_tunisia';

UPDATE fruits SET
    magnesium_mg = 10,  calcium_mg = 40,  iron_mg = 0.10, fructose_g = 2.65, orac_umol_te = 726
    WHERE code = 'orange_gannan';

UPDATE fruits SET
    magnesium_mg = 11,  calcium_mg = 13,  iron_mg = 0.36, fructose_g = 5.37, orac_umol_te = 4873
    WHERE code = 'cherry_yantai';
