const auth = require('./utils/auth.js');
const env = require('./config/env.js');

App({
  globalData: {
    apiBase: env.apiBase,
    assetBase: env.assetBase,
    env: env.ENV,
    subscribeTemplates: {
      shipment: '',
      delivery: '',
    },
  },

  onLaunch() {
    this.globalData.token = auth.getToken();
    this.globalData.user = auth.getUser();
    // 游客模式：未登录也允许停留在首页，首页自己处理"登录提醒 CTA"
  },
});
