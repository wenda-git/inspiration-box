-- Fruit-level operating controls:
-- 1) allergy_tags: normalized allergen/family labels
-- 2) value_tier: basic / highlight / premium
-- 3) selection_status + ops_weight: weekly push/downrank/pause controls

ALTER TABLE fruits ADD COLUMN IF NOT EXISTS allergy_tags TEXT[] NOT NULL DEFAULT '{}'::text[];
ALTER TABLE fruits ADD COLUMN IF NOT EXISTS value_tier VARCHAR(20) NOT NULL DEFAULT 'basic';
ALTER TABLE fruits ADD COLUMN IF NOT EXISTS selection_status VARCHAR(20) NOT NULL DEFAULT 'normal';
ALTER TABLE fruits ADD COLUMN IF NOT EXISTS ops_weight INT NOT NULL DEFAULT 0;
ALTER TABLE fruits ADD COLUMN IF NOT EXISTS ops_note TEXT;

UPDATE fruits
   SET value_tier = 'basic',
       selection_status = 'normal',
       ops_weight = 0
 WHERE value_tier IS NULL
    OR selection_status IS NULL;

-- Allergen/family tags used by the selector. Keep these concise and lowercase.
UPDATE fruits SET allergy_tags = ARRAY['berry'] WHERE code IN ('blueberry_yunnan', 'strawberry_danong');
UPDATE fruits SET allergy_tags = ARRAY['kiwi'] WHERE code IN ('kiwi_gold');
UPDATE fruits SET allergy_tags = ARRAY['citrus'] WHERE category = '柑橘' OR code IN ('orange_gannan', 'grapefruit_red');
UPDATE fruits SET allergy_tags = ARRAY['latex'] WHERE code IN ('avocado_hass');
UPDATE fruits SET allergy_tags = ARRAY['passionfruit'] WHERE code IN ('passionfruit');
UPDATE fruits SET allergy_tags = ARRAY['pomegranate'] WHERE code IN ('pomegranate_tunisia');

-- Value tiers for operating and box composition.
UPDATE fruits
   SET value_tier = 'premium'
 WHERE code IN ('blueberry_yunnan', 'cherry_yantai', 'avocado_hass');

UPDATE fruits
   SET value_tier = 'highlight'
 WHERE code IN ('kiwi_gold', 'pomegranate_tunisia', 'strawberry_danong', 'passionfruit')
   AND value_tier <> 'premium';

-- Strategy defaults: allow several highlight fruits, but at most one premium fruit.
UPDATE subscription_plans
   SET soft_prefs = COALESCE(soft_prefs, '{}'::jsonb) || '{
     "max_highlight_items": 3,
     "max_premium_items": 1,
     "premium_unit_cost_cny_per_kg": 65
   }'::jsonb,
       updated_at = now();
