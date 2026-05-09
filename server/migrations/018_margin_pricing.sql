-- Margin-oriented default pricing.
-- price_cny is the recurring weekly price; PLAN_FIRST_PAYMENT_CNY can still be used as an acquisition offer.
-- budget_cny is the hard fruit-cost cap used by the selector.

UPDATE subscription_plans
   SET name = '控糖轻盈方向',
       price_cny = 198,
       budget_cny = 90,
       box_target_g = 1800,
       soft_prefs = COALESCE(soft_prefs, '{}'::jsonb) || '{
         "min_items": 3,
         "max_items": 5,
         "single_share_max": 0.38,
         "target_fill_ratio": 0.82,
         "max_total_ratio": 1.05,
         "premium_unit_cost_cny_per_kg": 65,
         "max_premium_items": 1,
         "estimated_ops_cost_cny": 42,
         "target_contribution_margin": 0.25
       }'::jsonb,
       updated_at = now()
 WHERE code = 'fatloss_gi';

UPDATE subscription_plans
   SET name = '熬夜抗氧方向',
       price_cny = 198,
       budget_cny = 105,
       box_target_g = 2000,
       soft_prefs = COALESCE(soft_prefs, '{}'::jsonb) || '{
         "min_items": 3,
         "max_items": 5,
         "single_share_max": 0.38,
         "target_fill_ratio": 0.82,
         "max_total_ratio": 1.05,
         "premium_unit_cost_cny_per_kg": 65,
         "max_premium_items": 1,
         "estimated_ops_cost_cny": 45,
         "target_contribution_margin": 0.24
       }'::jsonb,
       updated_at = now()
 WHERE code = 'antiox';

UPDATE subscription_plans
   SET name = '孕产温和方向',
       price_cny = 198,
       budget_cny = 110,
       box_target_g = 2100,
       soft_prefs = COALESCE(soft_prefs, '{}'::jsonb) || '{
         "min_items": 3,
         "max_items": 5,
         "single_share_max": 0.38,
         "target_fill_ratio": 0.82,
         "max_total_ratio": 1.05,
         "premium_unit_cost_cny_per_kg": 65,
         "max_premium_items": 1,
         "estimated_ops_cost_cny": 48,
         "target_contribution_margin": 0.20
       }'::jsonb,
       updated_at = now()
 WHERE code = 'prenatal';

UPDATE subscriptions
   SET recurring_cny = 198,
       updated_at = now()
 WHERE status IN ('unpaid', 'trial', 'active', 'paused');
