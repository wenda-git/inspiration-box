const auth = require('./utils/auth.js');
const env = require('./config/env.js');

App({
  globalData: {
    apiBase: env.apiBase,
    assetBase: env.assetBase,
    env: env.ENV,
    useCloudContainer: env.useCloudContainer,
    cloudEnvId: env.cloudEnvId,
    cloudService: env.cloudService,
    cloudPublicBase: env.cloudPublicBase,
    subscribeTemplates: {
      shipment: '',
      delivery: '',
    },
  },

  onLaunch() {
    if (env.useCloudContainer) {
      if (!wx.cloud) {
        console.error('当前基础库不支持 wx.cloud，请升级微信基础库');
      } else {
        wx.cloud.init({
          env: env.cloudEnvId,
          traceUser: true,
        });
      }
    }
    this.globalData.token = auth.getToken();
    this.globalData.user = auth.getUser();
    // 游客模式：未登录也允许停留在首页，首页自己处理"登录提醒 CTA"
  },
});
