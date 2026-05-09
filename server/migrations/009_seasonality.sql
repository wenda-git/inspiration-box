-- =========================================================
-- Migration 009: 时令 + 产地
-- =========================================================

ALTER TABLE fruits
    ADD COLUMN IF NOT EXISTS origin        VARCHAR(80),              -- "云南"/"山东烟台"
    ADD COLUMN IF NOT EXISTS origin_region VARCHAR(40),              -- "south_china"/"north_china"/"imported"
    ADD COLUMN IF NOT EXISTS season_months INT[] DEFAULT '{}'::INT[]; -- 月份数组 [1..12]，空表示全年

-- 回填现有水果的时令和产地
UPDATE fruits SET origin='云南',     origin_region='south_china', season_months='{5,6,7,8}'::int[]          WHERE code='blueberry_yunnan';
UPDATE fruits SET origin='福建平和', origin_region='south_china', season_months='{10,11,12,1,2}'::int[]      WHERE code='grapefruit_red';
UPDATE fruits SET origin='山东烟台', origin_region='north_china', season_months='{9,10,11,12,1}'::int[]      WHERE code='apple_fuji';
UPDATE fruits SET origin='新西兰',   origin_region='imported',    season_months='{4,5,6,7,8,9,10,11}'::int[] WHERE code='kiwi_gold';
UPDATE fruits SET origin='辽宁丹东', origin_region='north_china', season_months='{12,1,2,3,4,5}'::int[]      WHERE code='strawberry_danong';
UPDATE fruits SET origin='突尼斯',   origin_region='imported',    season_months='{9,10,11,12}'::int[]        WHERE code='pomegranate_tunisia';
UPDATE fruits SET origin='江西赣州', origin_region='south_china', season_months='{11,12,1,2}'::int[]         WHERE code='orange_gannan';
UPDATE fruits SET origin='山东烟台', origin_region='north_china', season_months='{5,6,7}'::int[]             WHERE code='cherry_yantai';
UPDATE fruits SET origin='墨西哥',   origin_region='imported',    season_months='{}'::int[]                  WHERE code='avocado_hass';
UPDATE fruits SET origin='台湾',     origin_region='imported',    season_months='{}'::int[]                  WHERE code='guava_taiwan';
UPDATE fruits SET origin='越南',     origin_region='imported',    season_months='{7,8,9,10,11}'::int[]       WHERE code='passionfruit';
UPDATE fruits SET origin='越南',     origin_region='imported',    season_months='{5,6,7,8,9,10,11}'::int[]   WHERE code='dragonfruit_red';
