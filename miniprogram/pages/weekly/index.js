/**
 * 首页 Dashboard（样稿那版）
 * 结构：
 *   - 顶部 greeting
 *   - Hero: 本周一箱（水果盒插画 + CTA 进详情页）
 *   - 3 个特性 chips
 *   - 2×2 功能卡（本周推荐 / 营养提醒 / 我的订阅 / 本周心情）
 *   - 未付款时最底部浮一个订阅 CTA
 */
const api = require('../../utils/api.js');

const PLAN_LABEL = {
  fatloss_gi: '减脂控糖',
  prenatal: '孕产营养',
  antiox: '抗氧轻食',
};

const PLAN_TONE = {
  fatloss_gi: '控糖轻盈搭配',
  prenatal: '温和营养搭配',
  antiox: '抗氧轻食搭配',
};

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    loading: true,
    greeting: '',
    user: null,
    subscription: null,
    order: null,
    data: null,
    isPaid: false,
    firstPayment: 99,
    itemsPreview: '',
    planLabel: '',
    nextShipText: '',
    nutritionTip: null,
    feedbackLearning: null,
    // 顶部胶囊占位高度（状态栏 + 胶囊高度 + 8rpx 间距）
    capsuleTop: 44,
    statusBarHeight: 20,
    navHeight: 88,
    isLoggedIn: false,
  },

  onLoad() {
    try {
      const sys = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const menu = wx.getMenuButtonBoundingClientRect && wx.getMenuButtonBoundingClientRect();
      const statusBarHeight = sys.statusBarHeight || 20;
      const capsuleTop = menu ? menu.top : statusBarHeight + 8;
      const capsuleHeight = menu ? menu.height : 32;
      this.setData({
        statusBarHeight,
        capsuleTop,
        navHeight: capsuleTop + capsuleHeight + 8,
      });
    } catch (e) {}
  },

  async onShow() {
    const auth = require('../../utils/auth.js');
    const isLoggedIn = auth.isLoggedIn();
    this.setData({ isLoggedIn });
    // 游客也能进首页
    this.fetch(isLoggedIn);
  },

  onPullDownRefresh() {
    const auth = require('../../utils/auth.js');
    this.fetch(auth.isLoggedIn()).then(() => wx.stopPullDownRefresh());
  },

  async fetch(isLoggedIn = false) {
    this.setData({ loading: true });
    try {
      // 游客：只拉 plans（无需鉴权）+ 给演示问候
      if (!isLoggedIn) {
        const plans = await api.listPlans().catch(() => []);
        const firstPayment = plans?.[0]?.first_payment_cny || 99;
        this.setData({
          loading: false,
          data: null,
          subscription: null,
          order: null,
          isPaid: false,
          hasProfile: false,
          firstPayment,
          itemsPreview: '4-5 种时令水果 · 个性化搭配',
          planLabel: '春日轻盈果箱',
          nextShipText: this._nextThursdayText(),
          nutritionTip: null,
          feedbackLearning: null,
          greeting: this._greetingByHour('灵感生活家'),
        });
        return;
      }

      // 登录用户：按原流程
      const status = await api.getOnboardingStatus().catch(() => null);
      const hasProfile = !!(status && status.has_profile);

      const tasks = [
        hasProfile ? api.getWeeklyRecommendation().catch(() => null) : Promise.resolve(null),
        api.getCurrentSubscription().catch(() => null),
        api.getCurrentOrder().catch(() => null),
        api.listPlans().catch(() => []),
      ];
      const [data, subscription, order, plans] = await Promise.all(tasks);

      const nickname = (api && (require('../../utils/auth.js').getUser() || {}).nickname) || '灵感生活家';
      const greeting = this._greetingByHour(nickname);

      const items = (data && data.items) || [];
      const itemsPreview = items.length
        ? `${items.length} 种时令水果 · ${PLAN_TONE[data && data.plan_code] || '个性化搭配'}`
        : (hasProfile ? 'AI 为你轻轻挑好了' : '完成画像后为你生成');

      const highlight = this._pickHighlight(items);
      const feedbackLearning = this._normalizeFeedbackLearning(data);
      const nextShipText = this._nextThursdayText();
      const firstPayment = plans?.[0]?.first_payment_cny || 99;

      this.setData({
        loading: false,
        data,
        subscription,
        order,
        isPaid: !!(subscription && subscription.billing_state === 'paid'),
        firstPayment,
        itemsPreview,
        planLabel: PLAN_LABEL[data?.plan_code] || '春日轻盈果箱',
        nextShipText,
        nutritionTip: highlight,
        feedbackLearning,
        greeting,
        hasProfile,
      });
    } catch (e) {
      this.setData({ loading: false });
    }
  },

  _normalizeFeedbackLearning(data) {
    const learning = data && data.decision_trace && data.decision_trace.feedback_learning;
    const items = (learning && learning.items) || [];
    if (!items.length) return null;
    const first = items[0] || {};
    const summary = learning.summary || (
      items.length > 1
        ? `${first.message || '本周已应用你的反馈'}，另有 ${items.length - 1} 项偏好已调整`
        : (first.message || '本周已应用你的反馈')
    );
    return {
      title: learning.title || '已根据你的反馈调整',
      summary,
      count: items.length,
      firstEmoji: first.emoji || '✓',
      firstName: first.fruit_name_cn || '偏好',
    };
  },

  _greetingByHour(name) {
    const h = new Date().getHours();
    const hello =
      h < 6 ? '夜深了' :
      h < 11 ? '早上好' :
      h < 14 ? '中午好' :
      h < 18 ? '下午好' :
      '晚上好';
    return `${hello}，${name}`;
  },

  _pickHighlight(items) {
    if (!items || !items.length) return null;
    // 营养维度优先级：vitC → 花青素 → 纤维
    let best = null;
    let bestScore = -1;
    for (const it of items) {
      const n = it.nutrition_per_100g || {};
      const score = (n.vitC_mg || 0) * 1 + (n.anthocyanin_mg || 0) * 0.5 + (n.fiber_g || 0) * 3;
      if (score > bestScore) {
        bestScore = score;
        best = it;
      }
    }
    if (!best) return null;
    const n = best.nutrition_per_100g || {};
    let title = '维 C 补给';
    let detail = `${best.fruit_name_cn}富含维生素 C，有助于提升免疫力。`;
    if (n.anthocyanin_mg >= 50) {
      title = '抗氧花青素';
      detail = `${best.fruit_name_cn}花青素 ${n.anthocyanin_mg}mg/100g，修复熬夜氧化压力。`;
    } else if (n.fiber_g >= 3) {
      title = '高纤维补给';
      detail = `${best.fruit_name_cn}膳食纤维 ${n.fiber_g}g/100g，对肠道友好。`;
    } else if (n.vitC_mg >= 40) {
      title = '维 C 补给';
      detail = `${best.fruit_name_cn}富含维生素 C ${n.vitC_mg}mg/100g，提升免疫力。`;
    }
    return { title, detail, fruit_code: best.fruit_code, emoji: best.emoji };
  },

  _nextThursdayText() {
    const now = new Date();
    const day = now.getDay();
    const diff = (4 - day + 7) % 7 || 7;
    const t = new Date(now);
    t.setDate(now.getDate() + diff);
    const week = ['日','一','二','三','四','五','六'][t.getDay()];
    return `${t.getMonth() + 1}月${t.getDate()}日（周${week}）`;
  },

  async onGoDetail() {
    if (!this.data.isLoggedIn || !this.data.hasProfile) {
      wx.redirectTo({ url: '/pages/onboarding/index' });
      return;
    }
    wx.navigateTo({ url: '/pages/weekly/detail/index' });
  },

  onGoFeedback() {
    wx.switchTab({ url: '/pages/feedback/index' });
  },

  onGoSubscription() {
    wx.switchTab({ url: '/pages/profile/index' });
  },

  async onPay() {
    if (!this.data.isLoggedIn || !this.data.hasProfile) {
      wx.redirectTo({ url: '/pages/onboarding/index' });
      return;
    }
    wx.navigateTo({ url: '/pages/weekly/detail/index' });
  },
});
