const api = require('../../../utils/api.js');

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    loading: false,
    planName: '个性化果箱',
    sampleItems: [
      { emoji: '🫐', name: '蓝莓', tag: '花青素' },
      { emoji: '🍊', name: '橙子', tag: '维 C' },
      { emoji: '🥝', name: '猕猴桃', tag: '高纤维' },
    ],
    hits: [],
    reasons: [
      '先测状态，再生成本周果箱',
      '看到完整配单和推荐理由后再决定',
      '收货后的反馈会影响下周搭配',
    ],
  },

  onLoad(query = {}) {
    this._shareQuery = query;
    if (query.sid) {
      this.setData({ loading: true });
      this._loadShareReport(query.sid);
      return;
    }
    this._applyQueryReport(query);
  },

  async _loadShareReport(sid) {
    try {
      const report = await api.getShareReport(sid);
      const payload = (report && report.payload) || {};
      this.setData({
        loading: false,
        planName: payload.plan_name || '个性化果箱',
        sampleItems: (payload.items || []).slice(0, 3).map(it => ({
          emoji: it.emoji || '🍎',
          name: it.name || '本周水果',
          tag: it.tag || '已入箱',
        })),
        hits: (payload.hits || []).slice(0, 4),
        reasons: (payload.reasons || this.data.reasons).slice(0, 4),
      });
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: '分享已失效', icon: 'none' });
    }
  },

  _applyQueryReport(query = {}) {
    const patch = {};
    if (query.plan) {
      patch.planName = decodeURIComponent(query.plan);
    }
    if (query.items) {
      try {
        const parsed = JSON.parse(decodeURIComponent(query.items));
        if (Array.isArray(parsed) && parsed.length) {
          patch.sampleItems = parsed.slice(0, 3).map(it => ({
            emoji: it.e || '🍎',
            name: it.n || '本周水果',
            tag: it.t || '已入箱',
          }));
        }
      } catch (e) {}
    }
    if (query.hits) {
      patch.hits = decodeURIComponent(query.hits).split(',').filter(Boolean).slice(0, 4);
    }
    this.setData(patch);
  },

  onStart() {
    wx.redirectTo({ url: '/pages/onboarding/index' });
  },

  onHome() {
    wx.switchTab({ url: '/pages/weekly/index' });
  },

  onShareAppMessage() {
    const q = this._shareQuery || {};
    const params = [];
    if (q.sid) params.push(`sid=${q.sid}`);
    if (q.plan) params.push(`plan=${q.plan}`);
    if (q.items) params.push(`items=${q.items}`);
    if (q.hits) params.push(`hits=${q.hits}`);
    return {
      title: '测一测你这周适合吃什么水果',
      path: `/pages/share/report/index${params.length ? '?' + params.join('&') : ''}`,
      imageUrl: `${getApp().globalData.assetBase}/brand/share-cover.png`,
    };
  },
});
