/**
 * 付款成功页 · 本周一箱最重要的仪式感
 * 进入条件：首单支付 success 后跳转到这里
 * 离开方式：点"看看本周的箱子"去 weekly
 */
const api = require('../../utils/api.js');
const auth = require('../../utils/auth.js');

const PLAN_LABEL = {
  fatloss_gi: '减脂控糖',
  prenatal: '孕产营养',
  antiox: '抗氧轻食',
};
const PLAN_THEME = {
  fatloss_gi: 'matcha',
  prenatal: 'berry',
  antiox: 'peach',
};

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    loading: true,
    theme: 'matcha',
    planName: '',
    shipDayText: '',             // "本周四 · 5 月 8 日"
    highlights: [],              // 前 3 个水果
    subscription: null,
  },

  async onLoad() {
    if (!auth.isLoggedIn()) {
      auth.redirectToLogin();
      return;
    }
    try {
      const [sub, rec] = await Promise.all([
        api.getCurrentSubscription(),
        api.getWeeklyRecommendation().catch(() => null),
      ]);

      const planCode = sub?.plan_code || (rec && rec.plan_code);
      const items = (rec && rec.items) || [];
      this.setData({
        loading: false,
        subscription: sub,
        theme: PLAN_THEME[planCode] || 'matcha',
        planName: PLAN_LABEL[planCode] || '个性化方向',
        shipDayText: this._nextThursday(),
        highlights: items.slice(0, 3).map(it => ({
          fruit_name_cn: it.fruit_name_cn,
          emoji: it.emoji || '🍎',
        })),
      });
    } catch (e) {
      this.setData({ loading: false });
    }
  },

  // 计算最近的下一个周四（含今天）
  _nextThursday() {
    const now = new Date();
    const day = now.getDay(); // 0 日 ~ 6 六
    const diff = (4 - day + 7) % 7; // 到周四的天数
    const t = new Date(now);
    t.setDate(now.getDate() + diff);
    const mm = t.getMonth() + 1;
    const dd = t.getDate();
    const weekName = diff === 0 ? '今天' : (diff === 1 ? '明天' : '本周四');
    return `${weekName} · ${mm} 月 ${dd} 日`;
  },

  onGoWeekly() {
    wx.redirectTo({ url: '/pages/weekly/detail/index' });
  },

  onSubscribeMessages() {
    const templates = (getApp().globalData && getApp().globalData.subscribeTemplates) || {};
    const tmplIds = [templates.shipment, templates.delivery].filter(Boolean);
    if (!tmplIds.length) {
      wx.showToast({ title: '订阅模板待配置', icon: 'none' });
      return;
    }
    wx.requestSubscribeMessage({
      tmplIds,
      success(res) {
        const accepted = tmplIds.some(id => res[id] === 'accept');
        wx.showToast({
          title: accepted ? '已开启提醒' : '未开启提醒',
          icon: 'none',
        });
      },
      fail() {
        wx.showToast({ title: '订阅授权失败', icon: 'none' });
      },
    });
  },

  onShare() {
    wx.showShareMenu({ withShareTicket: false });
    wx.showModal({
      title: '分享给朋友',
      content: '点击右上角菜单，或使用下方「分享给朋友」按钮即可分享。',
      showCancel: false,
    });
  },

  onShareAppMessage() {
    return {
      title: '我在 灵感果仓 订阅了每周的水果盲盒',
      path: '/pages/login/index',
    };
  },
});
