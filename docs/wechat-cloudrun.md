# 微信云托管部署说明

本项目后端是 FastAPI 容器服务，云托管部署目录选择 `server/`。

## 1. 部署前准备

云托管不能连接你电脑里的 Docker Postgres。先准备一个云上 PostgreSQL，并把本地迁移 SQL 跑到云数据库：

```bash
psql "$DATABASE_URL" -f server/migrations/001_init.sql
psql "$DATABASE_URL" -f server/migrations/002_auth_and_addresses.sql
psql "$DATABASE_URL" -f server/migrations/003_profile_and_subscription.sql
psql "$DATABASE_URL" -f server/migrations/004_weekly_cache.sql
psql "$DATABASE_URL" -f server/migrations/005_catalog.sql
psql "$DATABASE_URL" -f server/migrations/006_admin.sql
psql "$DATABASE_URL" -f server/migrations/007_more_fruits.sql
psql "$DATABASE_URL" -f server/migrations/008_feedbacks.sql
psql "$DATABASE_URL" -f server/migrations/009_feedback_signals.sql
psql "$DATABASE_URL" -f server/migrations/010_payment.sql
psql "$DATABASE_URL" -f server/migrations/011_addresses_region.sql
psql "$DATABASE_URL" -f server/migrations/012_orders.sql
psql "$DATABASE_URL" -f server/migrations/013_admin_password.sql
psql "$DATABASE_URL" -f server/migrations/014_admin_metrics.sql
psql "$DATABASE_URL" -f server/migrations/015_preference_signals.sql
psql "$DATABASE_URL" -f server/migrations/016_share_reports.sql
psql "$DATABASE_URL" -f server/migrations/017_operations_backbone.sql
psql "$DATABASE_URL" -f server/migrations/018_margin_pricing.sql
psql "$DATABASE_URL" -f server/migrations/019_fruit_operating_controls.sql
```

## 2. 云托管环境变量

在云托管服务的环境变量里配置：

```env
APP_ENV=dev
PORT=8000
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME
JWT_SECRET=请重新生成
JWT_TTL_DAYS=30
SMS_CODE_SALT=请重新生成

SMS_PROVIDER=aliyun_pnvs
SMS_ACCESS_KEY=你的AccessKeyId
SMS_ACCESS_SECRET=你的AccessKeySecret
SMS_SIGN_NAME=号码认证赠送签名
SMS_TEMPLATE_ID=100001
SMS_REGION_ID=cn-hangzhou
SMS_ENDPOINT=dypnsapi.aliyuncs.com
SMS_VALID_TIME=300

OPENAI_API_KEY=你的OpenAI Key
OPENAI_MODEL=gpt-4o

OSS_BUCKET=inspiration-images
OSS_ENDPOINT=oss-cn-beijing.aliyuncs.com
OSS_ASSET_PREFIX=miniprogram/assets
OSS_ACCESS_KEY=你的OSS AccessKeyId
OSS_ACCESS_SECRET=你的OSS AccessKeySecret
OSS_SIGNED_URL_TTL=3600

WX_PAY_PROVIDER=mock
PLAN_FIRST_PAYMENT_CNY=99

ADMIN_USERNAME=admin
ADMIN_PASSWORD=请重新设置
ADMIN_SESSION_SECRET=请重新生成
```

生产环境把 `APP_ENV=production`，并确保所有 secret 都不是 `dev-`、`admin` 或示例值。

## 3. 云托管构建设置

- 部署目录：`server/`
- 构建方式：Dockerfile
- 监听端口：`8000` 或环境变量 `PORT`
- 健康检查路径可用：`/v1/plans`

本地验证构建：

```bash
docker build -t inspiration-box-server:test server
```

## 4. 小程序切到云托管

修改 [miniprogram/config/env.js](../miniprogram/config/env.js)：

```js
const ENV = 'cloud';

const CONFIG = {
  cloud: {
    apiBase: 'https://你的云托管域名',
  },
};
```

`assetBase` 会自动变成：

```text
https://你的云托管域名/v1/assets
```

## 5. 微信后台配置

如果使用 `wx.request` 调云托管公网域名，需要在小程序后台把云托管域名加入 request 合法域名。

OSS Bucket 保持私有即可，小程序不直接访问裸 OSS 地址，而是访问后端 `/v1/assets/...`。
