const api = require('../../utils/api.js');
const auth = require('../../utils/auth.js');

function maskPhone(p) {
  if (!p) return '未绑定手机号';
  // +8613812345678 → 138****5678
  const tail = p.slice(-11);
  return tail.slice(0, 3) + '****' + tail.slice(-4);
}

Page({
  data: {
    user: {},
    maskedPhone: '',
    defaultAddr: null,
    subscription: null,
    nextChargeStr: '',
    daysSubscribed: 0,
    milestones: [],
    preferenceCards: [],
    firstPayment: 99,
    showDirectionPicker: false,
    isLoggedIn: false,
  },

  onShow() {
    const isLoggedIn = auth.isLoggedIn();
    this.setData({ isLoggedIn });
    if (!isLoggedIn) {
      // 游客模式：不拉数据
      this.setData({
        user: {},
        maskedPhone: '',
        defaultAddr: null,
        subscription: null,
        preferenceCards: [],
      });
      return;
    }
    const user = auth.getUser() || {};
    this.setData({
      user,
      maskedPhone: maskPhone(user.phone_e164),
    });
    this._loadDefaultAddr();
    this._loadSubscription();
    this._loadFirstPayment();
    this._loadPreferences();
  },

  onGoLogin() {
    wx.navigateTo({ url: '/pages/login/index' });
  },

  async _loadFirstPayment() {
    try {
      const plans = await api.listPlans();
      if (plans && plans[0] && plans[0].first_payment_cny) {
        this.setData({ firstPayment: plans[0].first_payment_cny });
      }
    } catch (e) {}
  },

  async _loadSubscription() {
    try {
      const sub = await api.getCurrentSubscription();
      let nextChargeStr = '';
      let daysSubscribed = 0;
      let milestones = [];
      if (sub && sub.next_charge_at) {
        const d = new Date(sub.next_charge_at);
        nextChargeStr = `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
      }
      if (sub && sub.first_paid_at) {
        daysSubscribed = Math.floor(
          (Date.now() - new Date(sub.first_paid_at).getTime()) / 86400000
        );
        milestones = this._buildMilestones(daysSubscribed);
      }
      const statusText = sub && sub.status === 'paused'
        ? `已暂停至 ${sub.pause_until || '下周'}`
        : '订阅中';
      this.setData({ subscription: sub, nextChargeStr, daysSubscribed, milestones, statusText });
    } catch (e) {}
  },

  async _loadPreferences() {
    try {
      const rows = await api.getFeedbackPreferences();
      this.setData({ preferenceCards: this._formatPreferences(rows || []) });
    } catch (e) {
      this.setData({ preferenceCards: [] });
    }
  },

  _formatPreferences(rows) {
    const cards = [];
    for (const r of rows) {
      const weight = Number(r.weight || 0);
      let title = '';
      let detail = '';
      let tone = weight < 0 ? 'avoid' : 'like';
      if (r.signal_type === 'fruit') {
        if (weight < 0) {
          title = '不太喜欢某种水果';
          detail = `已降低 ${r.signal_key} 的配单权重`;
        } else {
          title = '喜欢某种水果';
          detail = `会保留 ${r.signal_key} 的相近口味`;
        }
      } else if (r.signal_type === 'taste') {
        if (r.signal_key === 'sweet') {
          title = '不喜欢太甜';
          detail = '后续会降低高甜水果权重';
          tone = 'avoid';
        } else if (r.signal_key === 'sour') {
          title = '不喜欢太酸';
          detail = '后续会减少酸口水果';
          tone = 'avoid';
        }
      } else if (r.signal_type === 'freshness') {
        title = '在意新鲜度';
        detail = '后续会更注意批次和熟度';
        tone = 'fresh';
      } else if (r.signal_type === 'amount') {
        const parts = String(r.signal_key || '').split(':');
        title = parts[0] === 'more' ? '希望分量更多' : '希望分量更少';
        detail = parts[1] ? `针对 ${parts[1]} 调整份量` : '后续会调整单品份量';
        tone = 'amount';
      }
      if (!title) continue;
      cards.push({
        title,
        detail,
        weight: weight.toFixed(1),
        source_count: r.source_count || 1,
        tone,
      });
      if (cards.length >= 6) break;
    }
    return cards;
  },

  _buildMilestones(days) {
    const all = [
      { at: 7,   icon: '📘', label: '订阅 7 天',  reward: '解锁《当季水果食用手册》', unlocked: days >= 7 },
      { at: 14,  icon: '🎁', label: '订阅 14 天', reward: '赠送一次「加料指定权」',    unlocked: days >= 14 },
      { at: 28,  icon: '🌟', label: '订阅 28 天', reward: '免费升级一次精品加料', unlocked: days >= 28 },
      { at: 56,  icon: '👑', label: '订阅 2 个月', reward: '专属营养师 1v1 咨询',       unlocked: days >= 56 },
      { at: 90,  icon: '💎', label: '订阅 3 个月', reward: '终身 9 折 + 裂变分红权',    unlocked: days >= 90 },
    ];
    return all.map(m => ({ ...m, days_left: Math.max(0, m.at - days) }));
  },

  onGoPlans() {
    // C 方案：不再有方向选择 tab，引导回问卷或 weekly
    wx.switchTab({ url: '/pages/weekly/index' });
  },

  onGoOnboarding() {
    wx.redirectTo({ url: '/pages/onboarding/index' });
  },

  onGoWeekly() {
    wx.switchTab({ url: '/pages/weekly/index' });
  },

  onSwitchDirection() {
    this.setData({ showDirectionPicker: true });
  },

  onCloseDirection() {
    this.setData({ showDirectionPicker: false });
  },

  async onPickDirection(e) {
    const v = e.currentTarget.dataset.v;
    if (this.data.subscription && this.data.subscription.plan_code === v) {
      this.setData({ showDirectionPicker: false });
      wx.showToast({ title: '这就是你当前的方向', icon: 'none' });
      return;
    }
    const names = { fatloss_gi: '减脂控糖', prenatal: '孕产营养', antiox: '抗氧轻食' };
    wx.showModal({
      title: '确认换方向？',
      content: `新方向「${names[v] || v}」从下周配单起生效。`,
      success: async (res) => {
        if (!res.confirm) {
          this.setData({ showDirectionPicker: false });
          return;
        }
        try {
          await api.changeSubscriptionPlan(this.data.subscription.id, v);
          wx.showToast({ title: '已切换，下周生效', icon: 'success' });
          this.setData({ showDirectionPicker: false });
          this._loadSubscription();
        } catch (e) {
          this.setData({ showDirectionPicker: false });
        }
      },
    });
  },

  onCancelSub() {
    if (!this.data.subscription) return;
    const id = this.data.subscription.id;
    wx.showModal({
      title: '确认取消订阅？',
      content: '取消后不再自动扣费，本周配单仍会正常送达。',
      confirmText: '取消订阅',
      confirmColor: '#D95A4A',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await api.cancelSubscription(id);
          wx.showToast({ title: '已取消', icon: 'success' });
          this._loadSubscription();
        } catch (e) {}
      },
    });
  },

  onPauseSub() {
    if (!this.data.subscription) return;
    const id = this.data.subscription.id;
    wx.showModal({
      title: '暂停下周配送？',
      content: '暂停后下周不扣费、不发货，之后可随时恢复。',
      confirmText: '暂停一周',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await api.pauseSubscription(id, 1, '用户主动暂停');
          wx.showToast({ title: '已暂停一周', icon: 'success' });
          this._loadSubscription();
        } catch (e) {}
      },
    });
  },

  async onResumeSub() {
    if (!this.data.subscription) return;
    try {
      await api.resumeSubscription(this.data.subscription.id);
      wx.showToast({ title: '已恢复订阅', icon: 'success' });
      this._loadSubscription();
    } catch (e) {}
  },

  async _loadDefaultAddr() {
    try {
      const list = await api.listAddresses();
      const def = list.find((a) => a.is_default) || list[0] || null;
      this.setData({ defaultAddr: def });
    } catch (e) {}
  },

  onAddresses() {
    wx.navigateTo({ url: '/pages/addresses/list/index' });
  },

  onOrders() {
    wx.navigateTo({ url: '/pages/orders/list/index' });
  },

  onSubscription() {
    if (this.data.subscription) {
      wx.navigateTo({ url: '/pages/weekly/detail/index' });
    } else {
      wx.navigateTo({ url: '/pages/onboarding/index' });
    }
  },

  onFeedbackHistory() {
    wx.navigateTo({ url: '/pages/feedback/history/index' });
  },

  onGoFeedback() {
    wx.switchTab({ url: '/pages/feedback/index' });
  },

  onAbout() {
    wx.showModal({
      title: '关于 灵感果仓',
      content: '灵感果仓是一款 AI 每周水果订阅小程序：先了解你的状态和口味，再结合时令、营养和新鲜度生成本周果箱。反馈会持续影响后续配单。',
      showCancel: false,
      confirmText: '知道了',
    });
  },

  onContact() {
    wx.showModal({
      title: '联系客服',
      content: '客服微信：inspirationbox-cs\n工作日 9:00-18:00',
      showCancel: false,
    });
  },

  onLogout() {
    wx.showModal({
      title: '退出登录？',
      content: '下次打开需要重新输入手机号。',
      success: (res) => {
        if (res.confirm) {
          auth.clearSession();
          getApp().globalData.token = '';
          getApp().globalData.user = null;
          wx.reLaunch({ url: '/pages/login/index' });
        }
      },
    });
  },
});
