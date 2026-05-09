# 灵感果仓 MVP API 契约 v0.2

## 约定

| 项 | 规范 |
|---|---|
| Base URL | `http://localhost:8000`（开发） / `https://api.inspirationbox.app`（生产） |
| 鉴权 | `Authorization: Bearer <jwt>`，手机号 + 短信验证码登录换取 |
| Content-Type | `application/json; charset=utf-8` |
| 幂等 | 写操作客户端自动附加 `Idempotency-Key: <uuidv4>` |
| 错误响应 | `{"detail": {"code": "STRING_CODE", "message": "人话"}}` |
| 时间 | 全部 ISO 8601 + 时区，例：`2026-05-07T14:00:00+08:00` |

### 通用错误码

| code | HTTP | 含义 |
|---|---|---|
| `AUTH_INVALID` / `AUTH_EXPIRED` | 401 | JWT 失效或缺失 |
| `VALIDATION_FAILED` | 422 | 参数校验失败（中文消息） |
| `PROFILE_REQUIRED` | 422 | 没填健康画像 |
| `SUBSCRIPTION_REQUIRED` | 422 | 没选订阅套餐 |
| `INVENTORY_INSUFFICIENT` | 422 | 库存不足以满足套餐硬约束 |
| `REGEN_LIMIT` | 429 | 本周"换一批"次数已达上限 |
| `SMS_COOLDOWN` / `SMS_DAILY_CAP` | 429 | 短信限流 |
| `LLM_UPSTREAM_ERROR` | 502 | LLM 异常，可重试 |

---

## 1. 鉴权

### `POST /v1/auth/request-code` — 发送短信验证码
```json
{ "phone": "13812345678" }
```
→ `{ "cooldown_sec": 60, "expires_at": "..." }`

### `POST /v1/auth/verify-code` — 校验并换 JWT
```json
{ "phone": "13812345678", "code": "123456" }
```
→ `{ "token": "eyJ...", "user": { "id": "uuid", "phone_e164": "+8613812345678", "nickname": null, "avatar_url": null } }`

---

## 2. 健康画像

### `GET /v1/profile`
→ `Profile` 对象 或 `null`

### `POST /v1/profile`
```json
{
  "gender": "female",
  "height_cm": 165,
  "weight_kg": 58,
  "goals": ["weight_loss", "fatigue"],
  "allergies": ["芒果"],
  "dislikes": [],
  "prenatal_stage": null
}
```
→ 201 `Profile` with `bmi` 计算字段

---

## 3. 收货地址

- `GET /v1/addresses` → `Address[]`
- `POST /v1/addresses` → 201 `Address`
- `PUT /v1/addresses/{id}` → `Address`
- `DELETE /v1/addresses/{id}` → 204
- `POST /v1/addresses/{id}/default` → `Address`

---

## 4. 套餐

### `GET /v1/plans`
返回可用套餐（从 `subscription_plans` 表读）：
```json
[
  {
    "code": "fatloss_gi",
    "name": "减脂控糖",
    "tagline": "低 GI · 高纤维",
    "price_cny": 128.0,
    "first_payment_cny": 99.0,
    "theme": "matcha",
    "emoji": "🥝",
    "bullets": ["加权 GI ≤ 45", "糖 ≤ 10g / 100g", "膳食纤维 ≥ 2.0g / 100g"]
  }
]
```

---

## 5. 订阅

### `GET /v1/subscriptions/current`
→ `Subscription` 或 `null`

### `POST /v1/subscriptions` — 建订阅（不付款）
```json
{ "plan_code": "fatloss_gi", "address_id": null }
```
→ 201 `Subscription` with `billing_state: "unpaid"`

已有 `unpaid` 订阅时会自动更新 `plan_code`，不会重复建。

### `POST /v1/subscriptions/{id}/pay` — 发起首单支付
→
```json
{
  "payment_id": "uuid",
  "out_trade_no": "VL1746...",
  "prepay_id": "mock_prepay_...",
  "amount_cny": 99.0,
  "provider": "mock",
  "auto_success": true,
  "subscription": { ... }
}
```
mock 模式下 `auto_success=true` 表示后端已标 paid，前端跳过 `wx.requestPayment`。

### `POST /v1/subscriptions/{id}/cancel`
→ `Subscription` with `status: "cancelled"`

### `POST /v1/subscriptions/{id}/address`
```json
{ "address_id": "uuid" }
```
绑定或更换订阅收货地址。支付和锁单前必须已有地址。

---

## 6. 配单

### `GET /v1/recommend/current?refresh=false`

**约束**：
- `refresh=false`（默认）：读本周缓存；没缓存就 LLM 跑一次
- `refresh=true`：强制重跑（"换一批"），每周限 2 次

**响应**（节选关键字段）：
```json
{
  "recommendation_id": "uuid",
  "subscription_id": "uuid",
  "plan_code": "fatloss_gi",
  "plan_name": "减脂控糖 · 低 GI 高纤维",
  "iso_year": 2026,
  "iso_week": 19,
  "items": [
    {
      "fruit_code": "blueberry_yunnan",
      "fruit_name_cn": "云南蓝莓",
      "emoji": "🫐",
      "batch_id": "BB-2619-A",
      "qty_g": 500,
      "unit_price_cny_per_kg": 80.0,
      "reason": "..."
    }
  ],
  "nutrition_report": {
    "total_g": 2500,
    "weighted_avg_per_100g": {"kcal": 46, "sugar_g": 9.0, "fiber_g": 2.0, "gi": 32, "vitC_mg": 15.2},
    "hits": ["GI ≤ 45 ✓", "糖 ≤ 10g ✓"],
    "gaps": []
  },
  "guide_md": "...",
  "greeting": "...",
  "daily_plan": [{"day": "周一", "items": "...", "tip": "..."}],
  "total_cost_cny": 72.0,
  "bonus_items": [],
  "decision_trace": {
    "total_candidates": 8,
    "filtered": [{"name_cn": "西瓜", "emoji": "🍉", "reason": "此套餐禁用该水果"}],
    "repair_attempts": [],
    "seed": 123
  },
  "regen_count": 0,
  "regen_left": 2,
  "cached": false,
  "generated_at": "2026-05-07T10:00:00+08:00"
}
```

---

## 7. 反馈

### `POST /v1/feedback`
```json
{
  "order_id": "uuid，可选；优先绑定已签收订单",
  "fruit_code": "orange_gannan",
  "kind": "taste",
  "rating": 2,
  "comment": "太酸了，下周换一种",
  "photo_urls": []
}
```
- `kind` ∈ `taste | freshness | damage | allergy | preference | other`
- `rating` 1–5 整数，可选
- `kind=damage` 必须带 `photo_urls`
- 有 `order_id` 时，订单必须属于当前用户且状态为 `delivered`，`fruit_code` 必须属于该订单 `items_snapshot`
- 无 `order_id` 时，仅兼容当前周已签收订单，`fruit_code` 必须属于本周配单 items 或 bonus_items

→
```json
{ "feedback_id": "uuid", "will_affect_next_week": true, "auto_refund_cny": 0 }
```

`rating ≤ 2` 的水果会在接下来 14 天的配单中降权回避。

### `GET /v1/feedback/history?limit=20`
→ 反馈列表，按时间倒序

---

## 8. Me

### `GET /v1/me/onboarding-status`
→
```json
{
  "has_profile": true,
  "has_address": true,
  "has_subscription": true,
  "has_paid_subscription": false,
  "next_step": "weekly"
}
```

`next_step` ∈ `profile | weekly`。地址不阻塞试看配单，支付或锁单前再补齐。

---

## 9. Webhooks（外部回调）

- `POST /v1/wxpay/notify` — 微信支付结果回调
- `POST /v1/shunfeng/routes` — 顺丰轨迹推送

这两个端点**不需要 JWT**，由签名/验签机制保护（见 `integrations/wxpay.py` / `shunfeng.py`）。

---

## 版本与兼容

- 路径版本 `/v1`；破坏性变更走 `/v2`
- 非破坏性字段可在 Response 任意新增，客户端必须忽略未知字段
