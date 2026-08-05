# 微信云托管部署说明

本项目后端是 FastAPI 容器服务，云托管部署目录选择 `server/`。

## 1. 部署前准备

云托管不能连接你电脑里的 Docker Postgres。云开发控制台“数据库”页面是集合数据库，
也不能直接运行本项目的 PostgreSQL SQL。请先准备一个云上 PostgreSQL，并按仓库实际文件顺序执行迁移：

```bash
for migration_file in server/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration_file" || exit 1
done
```

## 2. 云托管环境变量

在云托管服务的环境变量里配置：

```env
APP_ENV=production
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
- 健康检查路径：`/healthz`
- 云开发环境 ID：`cloud1-d9g11sc8o22b23a2e`
- 建议服务名：`inspiration-box-api`
- 调试阶段可把最小实例数设为 0；需要稳定响应时设为 1

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
    transport: 'cloud-container',
    cloudEnvId: 'cloud1-d9g11sc8o22b23a2e',
    cloudService: 'inspiration-box-api',
    cloudPublicBase: 'https://云托管控制台复制的公网访问地址',
  },
};
```

业务 API 会通过 `wx.cloud.callContainer` 访问，不需要配置 request 合法域名。
静态图片仍通过公网 HTTPS 入口加载，`assetBase` 会自动变成：

```text
https://云托管公网访问地址/v1/assets
```

## 5. 微信后台配置

当前小程序和云开发环境属于同一个 AppID，不需要开启“环境共享”。只有跨小程序复用环境时才开环境共享。

OSS Bucket 保持私有即可，小程序不直接访问裸 OSS 地址，而是访问后端 `/v1/assets/...`。
真机加载 `<image>` 时，需要在小程序后台的 `downloadFile 合法域名` 中同时添加：

- 云托管公网 HTTPS 域名；
- OSS HTTPS 域名（后端图片接口会 302 跳转到签名后的 OSS 地址）。
