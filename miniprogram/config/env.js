const ENV = 'local';

const CONFIG = {
  local: {
    apiBase: 'http://localhost:8000',
  },
  cloud: {
    apiBase: 'https://你的云托管域名',
  },
};

const current = CONFIG[ENV] || CONFIG.local;

module.exports = {
  ENV,
  apiBase: current.apiBase,
  assetBase: `${current.apiBase}/v1/assets`,
};
