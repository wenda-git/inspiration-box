/**
 * 登录后 / 进入关键 tab 前的路由守卫
 *
 * 用法：
 *   const { routeNext } = require('../../utils/flow.js');
 *   await routeNext();
 */

const api = require('./api.js');
const auth = require('./auth.js');

// next_step → 小程序页面。地址按产品流程后置到支付/锁单前补齐。
const STEP_ROUTES = {
  profile: '/pages/onboarding/index',   // 问卷里选方向 + 画像一起搞定
  weekly:  '/pages/weekly/index',
};

// 哪些是 tab 页
const TAB_PAGES = new Set([
  'pages/weekly/index',
  'pages/feedback/index',
  'pages/profile/index',
]);

/**
 * 计算"下一步"：问卷（含方向）→ 本周正式配单。
 * 地址不阻塞进入本周，支付/锁单前由业务页单独校验。
 */
function computeNextStep(status) {
  if (!status.has_profile) return 'profile';
  if (!status.has_subscription) return 'profile';
  return 'weekly';
}

async function routeNext() {
  if (!auth.isLoggedIn()) {
    auth.redirectToLogin();
    return;
  }
  try {
    const status = await api.getOnboardingStatus();
    const step = computeNextStep(status);
    const target = STEP_ROUTES[step] || '/pages/weekly/index';
    const cleanTarget = target.replace(/^\//, '');

    if (TAB_PAGES.has(cleanTarget)) {
      wx.switchTab({ url: target });
    } else {
      wx.reLaunch({ url: target });
    }
    return status;
  } catch (e) {
    return null;
  }
}

/**
 * 进入 weekly / 其他内页前的守卫
 * 默认要求：已登录 + 画像 + 地址 + 已选健康方向。
 */
async function requireReady(required = ['profile', 'address', 'subscription']) {
  if (!auth.isLoggedIn()) {
    auth.redirectToLogin();
    return false;
  }
  try {
    const s = await api.getOnboardingStatus();
    const missing = {
      profile: !s.has_profile,
      address: !s.has_address,
      subscription: !s.has_subscription,
      paid: !s.has_paid_subscription,
    };
    for (const key of required) {
      if (missing[key]) {
        const step = computeNextStep(s);
        const target = STEP_ROUTES[step];
        const cleanTarget = target.replace(/^\//, '');
        if (TAB_PAGES.has(cleanTarget)) {
          wx.switchTab({ url: target });
        } else {
          wx.reLaunch({ url: target });
        }
        return false;
      }
    }
    return true;
  } catch (e) {
    return false;
  }
}

module.exports = { routeNext, requireReady, computeNextStep };
