/**
 * 交互级登录守卫
 *
 * 用法（在任何按钮 handler 里）：
 *   const { ensureLogin } = require('../../utils/guard.js');
 *   if (!await ensureLogin('开始订阅需要先登录')) return;
 *   // ...接着执行登录后的逻辑
 */
const auth = require('./auth.js');

function ensureLogin(reason = '登录后即可继续') {
  return new Promise((resolve) => {
    if (auth.isLoggedIn()) {
      resolve(true);
      return;
    }
    wx.showModal({
      title: '请先登录',
      content: reason,
      confirmText: '去登录',
      cancelText: '再看看',
      success: (res) => {
        if (res.confirm) {
          // 记录返回路径
          const pages = getCurrentPages();
          const cur = pages[pages.length - 1];
          const redirect = cur ? cur.route : '';
          wx.navigateTo({
            url: `/pages/login/index?redirect=${encodeURIComponent(redirect)}`,
          });
        }
        // 不论去不去登录，当前按钮动作都中断
        resolve(false);
      },
    });
  });
}

module.exports = { ensureLogin };
