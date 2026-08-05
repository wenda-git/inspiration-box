const ENV = 'cloud';

const CONFIG = {
  local: {
    transport: 'request',
    apiBase: 'https://127.0.0.1:8001',
  },
  cloud: {
    transport: 'cloud-container',
    cloudEnvId: 'cloud1-d9g11sc8o22b23a2e',
    cloudService: 'inspiration-box-api',
    // WXML 的 <image> 无法使用 callContainer，需要公网 HTTPS 地址加载素材。
    cloudPublicBase: 'https://inspiration-box-api-292205-8-1463813626.sh.run.tcloudbase.com',
  },
};

const current = CONFIG[ENV] || CONFIG.local;
const apiBase = current.apiBase || current.cloudPublicBase || '';

module.exports = {
  ENV,
  transport: current.transport,
  useCloudContainer: current.transport === 'cloud-container',
  cloudEnvId: current.cloudEnvId || '',
  cloudService: current.cloudService || '',
  cloudPublicBase: current.cloudPublicBase || '',
  apiBase,
  assetBase: `${current.cloudPublicBase || current.apiBase}/v1/assets`,
};
