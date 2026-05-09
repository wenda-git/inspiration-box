-- =========================================================
-- Migration 014: health_profiles 扩展生活习惯字段
-- 问卷里问了但之前没入库：蔬菜/睡眠/运动频率 + 口感偏好
-- =========================================================

ALTER TABLE health_profiles
    ADD COLUMN IF NOT EXISTS veg_freq       VARCHAR(20),
    ADD COLUMN IF NOT EXISTS sleep_pattern  VARCHAR(20),
    ADD COLUMN IF NOT EXISTS exercise_freq  VARCHAR(20),
    ADD COLUMN IF NOT EXISTS taste_prefer   TEXT[] NOT NULL DEFAULT '{}';
