const api = require('../../../utils/api.js');
const auth = require('../../../utils/auth.js');

const KIND_LABEL = {
  taste: '口味', freshness: '新鲜度', damage: '破损',
  allergy: '过敏', preference: '偏好', other: '其他',
};

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    loading: true,
    list: [],
  },

  async onShow() {
    if (!auth.isLoggedIn()) {
      auth.redirectToLogin();
      return;
    }
    this.setData({ loading: true });
    try {
      const raw = await api.getFeedbackHistory(50);
      const list = (raw || []).map(r => ({
        ...r,
        kind_label: KIND_LABEL[r.kind] || r.kind,
        stars: '★'.repeat(r.rating || 0) + '☆'.repeat(5 - (r.rating || 0)),
        created_str: this._fmt(r.created_at),
      }));
      this.setData({ list, loading: false });
    } catch (e) {
      this.setData({ loading: false });
    }
  },

  _fmt(s) {
    if (!s) return '';
    const d = new Date(s);
    return `${d.getMonth()+1} 月 ${d.getDate()} 日`;
  },
});
