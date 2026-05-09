const api = require('../../../utils/api.js');
const { getWeeklyRecommendation } = api;
const { parseMarkdown } = require('../../../utils/markdown.js');

const METRIC_DEFS = [
  { key: 'gi', label: 'GI 指数', target: 45, unit: '', invert: true },
  { key: 'sugar_g', label: '糖 / 100g', target: 10, unit: 'g', invert: true },
  { key: 'fiber_g', label: '纤维 / 100g', target: 3, unit: 'g', invert: false },
  { key: 'vitC_mg', label: '维 C / 100g', target: 40, unit: 'mg', invert: false },
];

const PLAN_THEME = {
  fatloss_gi: 'matcha',
  prenatal: 'berry',
  antiox: 'peach',
};

// AI 等待期的 4 段进度文案，每 4 秒切换一次
const LOADING_STAGES = [
  { text: '正在读取你的健康画像…' },
  { text: '扫描本周云仓库存…' },
  { text: '匹配硬约束和你的偏好…' },
  { text: '为你写一份食用指南…' },
];

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    loading: true,
    loadingStage: 0,
    loadingText: LOADING_STAGES[0].text,
    data: null,
    metrics: [],
    guideBlocks: [],
    theme: 'matcha',
    showTrace: false,
    showNutritionFull: false,
    // 订阅 + 支付
    subscription: null,
    isPaid: false,
    paying: false,
    firstPayment: 99,
    recurringPrice: 198,
    salesPoints: [],
    displayItems: [],
    visibleDailyPlan: [],
    showDailyFull: false,
    // 换一批额度
    regenLeft: 2,
    // 订单状态（已付款后接管 UI）
    order: null,
    shareId: '',
    // 付款确认弹层
    showPayConfirm: false,
  },

  onToggleTrace() {
    this.setData({ showTrace: !this.data.showTrace });
  },

  onToggleNutrition() {
    this.setData({ showNutritionFull: !this.data.showNutritionFull });
  },

  async onShow() {
    const auth = require('../../../utils/auth.js');
    const { requireReady } = require('../../../utils/flow.js');
    if (!auth.isLoggedIn()) {
      auth.redirectToLogin();
      return;
    }
    // 允许用户先试看配单；地址在支付/锁单前再补齐
    const ok = await requireReady(['profile']);
    if (!ok) return;
    this.fetch();
  },

  onLoad() {
    // onShow 会负责 fetch，这里不做事
  },

  onPullDownRefresh() {
    this.fetch().then(() => wx.stopPullDownRefresh());
  },

  _startLoadingTicker() {
    if (this._loadingTimer) clearInterval(this._loadingTimer);
    this.setData({
      loading: true,
      loadingStage: 0,
      loadingText: LOADING_STAGES[0].text,
    });
    this._loadingTimer = setInterval(() => {
      const next = (this.data.loadingStage + 1) % LOADING_STAGES.length;
      this.setData({
        loadingStage: next,
        loadingText: LOADING_STAGES[next].text,
      });
    }, 4000);
  },

  _stopLoadingTicker() {
    if (this._loadingTimer) {
      clearInterval(this._loadingTimer);
      this._loadingTimer = null;
    }
  },

  onUnload() {
    this._stopLoadingTicker();
  },

  async fetch(refresh = false) {
    this._startLoadingTicker();
    try {
      const data = await getWeeklyRecommendation(refresh);
      const avg = data.nutrition_report.weighted_avg_per_100g;
      const metrics = METRIC_DEFS.map((def) => {
        const value = avg[def.key] ?? 0;
        const raw = def.invert ? 1 - value / def.target : value / def.target;
        const pct = Math.max(8, Math.min(100, Math.round(raw * 100)));
        return {
          key: def.key,
          label: def.label,
          pct,
          value: `${value}${def.unit}`,
        };
      });
      this._stopLoadingTicker();
      const guideBlocks = parseMarkdown(data.guide_md || '');

      // 同时拉订阅状态 + 首单价 + 当前订单
      let subscription = null;
      let firstPayment = this.data.firstPayment;
      let recurringPrice = this.data.recurringPrice;
      let order = null;
      try {
        subscription = await api.getCurrentSubscription();
      } catch (e) {}
      try {
        const plans = await api.listPlans();
        if (plans && plans.length) {
          const plan = plans.find(p => p.code === data.plan_code) || plans[0];
          if (plan.first_payment_cny) firstPayment = plan.first_payment_cny;
          if (plan.price_cny) recurringPrice = plan.price_cny;
        }
      } catch (e) {}
      try {
        order = await api.getCurrentOrder();
      } catch (e) {}

      this.setData({
        loading: false,
        data,
        metrics,
        guideBlocks,
        subscription,
        isPaid: !!(subscription && subscription.billing_state === 'paid'),
        firstPayment,
        recurringPrice,
        salesPoints: this._buildSalesPoints(data),
        displayItems: this._buildDisplayItems(data),
        visibleDailyPlan: this._buildVisibleDailyPlan(data.daily_plan || [], false),
        showDailyFull: false,
        showNutritionFull: false,
        regenLeft: typeof data.regen_left === 'number' ? data.regen_left : 2,
        order,
        theme: PLAN_THEME[data.plan_code] || 'matcha',
      });
      this._prepareShareReport(data);
    } catch (e) {
      this._stopLoadingTicker();
      this.setData({ loading: false });
      if (e && e.code === 'REGEN_LIMIT') {
        // 额度用完，把本周的剩余次数刷新为 0 并重读缓存（非强制刷新）
        this.setData({ regenLeft: 0 });
        this.fetch(false);
        return;
      }
      // 业务错误已经是明确的文案（如"请先选择健康方向"），让 request.js 的 toast 自己处理
      if (e && (e.code === 'SUBSCRIPTION_REQUIRED' || e.code === 'PROFILE_REQUIRED')) {
        const { routeNext } = require('../../../utils/flow.js');
        routeNext();
      }
    }
  },

  _buildSalesPoints(data) {
    const items = data.items || [];
    const report = data.nutrition_report || {};
    const avg = report.weighted_avg_per_100g || {};
    const hits = report.hits || [];
    const first = items[0];
    const second = items[1];
    const points = [];
    if (typeof avg.gi === 'number' || typeof avg.sugar_g === 'number') {
      points.push({
        title: '糖控更稳',
        detail: `GI ${avg.gi ?? '-'} · 糖 ${avg.sugar_g ?? '-'}g/100g，适合一周加餐。`,
      });
    }
    if (hits.length) {
      points.push({
        title: '营养命中',
        detail: hits.slice(0, 2).join(' · '),
      });
    }
    if (first) {
      points.push({
        title: '本周主打',
        detail: `${first.fruit_name_cn}：${this._shortText(first.reason, 34)}`,
      });
    }
    if (second) {
      points.push({
        title: '口味搭配',
        detail: `${second.fruit_name_cn} 用来平衡这一箱的风味和分量。`,
      });
    }
    points.push({
      title: '下周会变',
      detail: '收货后的口味和新鲜度反馈，会进入下周配单权重。',
    });
    return points.slice(0, 4);
  },

  _buildDisplayItems(data) {
    const items = (data && data.items) || [];
    const planCode = data && data.plan_code;
    return items.map((it) => ({
      ...it,
      displayTag: this._inferItemTag(it, planCode),
      shortReason: this._shortText(it.reason || '适合本周搭配。', 42),
    }));
  },

  _inferItemTag(item, planCode) {
    const reason = item.reason || '';
    if (reason.indexOf('维 C') >= 0 || reason.indexOf('维生素 C') >= 0) return '维 C';
    if (reason.indexOf('纤维') >= 0 || reason.indexOf('高纤') >= 0) return '高纤';
    if (reason.indexOf('GI') >= 0 || reason.indexOf('控糖') >= 0 || planCode === 'fatloss_gi') return '低负担';
    if (reason.indexOf('花青素') >= 0 || reason.indexOf('抗氧') >= 0) return '抗氧';
    if (reason.indexOf('叶酸') >= 0 || planCode === 'prenatal') return '温和补给';
    return '本周推荐';
  },

  _shortText(text, maxLen) {
    const raw = String(text || '').replace(/\s+/g, '');
    if (!raw) return '';
    const first = raw.split(/[。；;]/)[0];
    const picked = first || raw;
    return picked.length > maxLen ? `${picked.slice(0, maxLen)}…` : picked;
  },

  _buildVisibleDailyPlan(plan, showFull) {
    const list = plan || [];
    return showFull ? list : list.slice(0, 3);
  },

  onToggleDailyPlan() {
    const showDailyFull = !this.data.showDailyFull;
    const full = (this.data.data && this.data.data.daily_plan) || [];
    this.setData({
      showDailyFull,
      visibleDailyPlan: this._buildVisibleDailyPlan(full, showDailyFull),
    });
  },

  onReplace(e) {
    const code = e.currentTarget.dataset.code;
    wx.showActionSheet({
      itemList: ['不吃这个，换一种', '数量减半', '保留'],
      success: (res) => {
        if (res.tapIndex < 2) {
          wx.showToast({ title: '已反馈，下周生效', icon: 'success' });
        }
      },
    });
  },

  onRegenerate() {
    // 已生成订单后不允许再换本周配单，后续通过反馈影响下周。
    if (this.data.order && this.data.order.status !== 'cancelled') {
      wx.showModal({
        title: '本周已锁单',
        content: '想换水果请在「反馈」里告诉 AI，下周生效。',
        confirmText: '去反馈',
        cancelText: '知道了',
        success: (res) => {
          if (res.confirm) wx.switchTab({ url: '/pages/feedback/index' });
        },
      });
      return;
    }
    if (this.data.regenLeft <= 0) {
      wx.showToast({ title: '本周换一批已用完', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '重新生成本周配单？',
      content: `AI 会基于你的画像重新挑一次，大约 20 秒。本周还能换 ${this.data.regenLeft} 次。`,
      success: (res) => {
        if (res.confirm) this.fetch(true);
      },
    });
  },

  // 用户点"立即订阅"——弹出底部确认抽屉
  onPay() {
    if (!this.data.subscription) {
      wx.showToast({ title: '订阅信息未加载', icon: 'none' });
      return;
    }
    if (this.data.paying || this.data.isPaid) return;
    this._ensureAddressThen(() => {
      this.setData({ showPayConfirm: true });
    });
  },

  onClosePayConfirm() {
    this.setData({ showPayConfirm: false });
  },

  // 真正触发支付
  async onConfirmPay() {
    if (this.data.paying) return;
    const subId = this.data.subscription && this.data.subscription.id;
    if (!subId) {
      wx.showToast({ title: '订阅信息未加载', icon: 'none' });
      return;
    }
    this.setData({ paying: true, showPayConfirm: false });
    wx.showLoading({ title: '唤起支付…', mask: true });
    try {
      const r = await api.paySubscription(subId);
      if (r.auto_success) {
        wx.hideLoading();
        setTimeout(() => {
          wx.redirectTo({ url: '/pages/paid/index' });
        }, 300);
        return;
      }
      if (r.payment_params) {
        wx.hideLoading();
        wx.requestPayment({
          ...r.payment_params,
          success: () => wx.redirectTo({ url: '/pages/paid/index' }),
          fail: () => {
            this.setData({ paying: false });
            wx.showToast({ title: '支付未完成', icon: 'none' });
          },
        });
        return;
      }
      wx.hideLoading();
      wx.showModal({
        title: '支付暂不可用',
        content: '当前服务还没有返回微信支付参数，请先确认后端支付配置或切换 mock 支付。',
        showCancel: false,
      });
      this.setData({ paying: false });
    } catch (e) {
      wx.hideLoading();
      this.setData({ paying: false });
    }
  },

  async _refreshAfterPay() {
    // 重拉订阅状态 + 不调 LLM（缓存已在，快）
    try {
      const subscription = await api.getCurrentSubscription();
      this.setData({
        subscription,
        isPaid: !!(subscription && subscription.billing_state === 'paid'),
        paying: false,
      });
    } catch (e) {
      this.setData({ paying: false });
    }
  },

  onRefreshOrder() {
    this.fetch(false);
  },

  async _ensureAddressThen(next) {
    try {
      const addresses = await api.listAddresses().catch(() => []);
      const address = (addresses || []).find(a => a.is_default) || (addresses || [])[0];
      if (!address) {
        wx.navigateTo({
          url: '/pages/addresses/edit/index?redirect=' + encodeURIComponent('pages/weekly/detail/index'),
        });
        return;
      }
      const sub = this.data.subscription || await api.getCurrentSubscription();
      if (sub && !sub.address_id) {
        try {
          const updated = await api.changeSubscriptionAddress(sub.id, address.id);
          this.setData({ subscription: updated });
        } catch (e) {}
      }
      next();
    } catch (e) {
      wx.navigateTo({
        url: '/pages/addresses/edit/index?redirect=' + encodeURIComponent('pages/weekly/detail/index'),
      });
    }
  },

  onGoFeedback() {
    wx.switchTab({ url: '/pages/feedback/index' });
  },

  async _prepareShareReport(data) {
    if (!data || !data.items || !data.items.length) return;
    const payload = this._buildSharePayload(data);
    try {
      const report = await api.createShareReport(payload, 'weekly');
      if (report && report.share_id) this.setData({ shareId: report.share_id });
    } catch (e) {}
  },

  _buildSharePayload(data) {
    return {
      plan_name: data.plan_name || '本周果箱',
      items: (data.items || []).slice(0, 6).map(it => ({
        emoji: it.emoji || '🍎',
        name: it.fruit_name_cn || '本周水果',
        tag: this._inferItemTag(it, data.plan_code),
      })),
      hits: (((data.nutrition_report || {}).hits || [])).slice(0, 4),
      reasons: [
        '先测状态，再生成本周果箱',
        '看到完整配单和推荐理由后再决定',
        '收货后的反馈会影响下周搭配',
      ],
    };
  },

  onShareAppMessage() {
    const data = this.data.data || {};
    const shareItems = (data.items || []).slice(0, 3).map(it => ({
      e: it.emoji,
      n: it.fruit_name_cn,
      t: this._inferItemTag(it, data.plan_code),
    }));
    const items = shareItems.map(it => it.n).join('、');
    const plan = encodeURIComponent(data.plan_name || '本周果箱');
    const hits = encodeURIComponent(((data.nutrition_report || {}).hits || []).slice(0, 3).join(','));
    const itemParam = encodeURIComponent(JSON.stringify(shareItems));
    const title = items
      ? `我这周适合吃：${items}`
      : '测一测你这周适合吃什么水果';
    const sid = this.data.shareId;
    return {
      title,
      path: sid
        ? `/pages/share/report/index?sid=${sid}`
        : `/pages/share/report/index?plan=${plan}&items=${itemParam}&hits=${hits}`,
      imageUrl: `${getApp().globalData.assetBase}/brand/share-cover.png`,
    };
  },
});
