const api = require('../../../utils/api.js');
const auth = require('../../../utils/auth.js');

const STATUS_LABEL = {
  locked: '已锁单',
  packing: '分拣中',
  shipped: '运输中',
  delivered: '已签收',
  cancelled: '已取消',
};

const STATUS_STEPS = [
  { key: 'locked', label: '确认配单' },
  { key: 'packing', label: '云仓分拣' },
  { key: 'shipped', label: '冷链发货' },
  { key: 'delivered', label: '签收反馈' },
];

Page({
  data: {
    id: '',
    loading: true,
    order: null,
    items: [],
    steps: [],
  },

  onLoad(query = {}) {
    if (!auth.isLoggedIn()) {
      auth.redirectToLogin();
      return;
    }
    this.setData({ id: query.id || '' });
    this.fetch();
  },

  onPullDownRefresh() {
    this.fetch().then(() => wx.stopPullDownRefresh());
  },

  async fetch() {
    const id = this.data.id;
    if (!id) {
      wx.showToast({ title: '订单不存在', icon: 'none' });
      this.setData({ loading: false });
      return;
    }
    this.setData({ loading: true });
    try {
      const order = await api.getOrder(id);
      const items = (order.items_snapshot || []).map(it => ({
        emoji: it.emoji || '🍎',
        name: it.fruit_name_cn || it.name || '本周水果',
        qty: it.qty_g || it.qty || 0,
        reason: it.reason || it.tag || '',
      }));
      this.setData({
        loading: false,
        order: {
          ...order,
          status_label: STATUS_LABEL[order.status] || order.status,
          week_text: `${order.iso_year} 第 ${order.iso_week} 周`,
          locked_at_str: this._fmtDateTime(order.locked_at),
          shipped_at_str: this._fmtDateTime(order.shipped_at),
          delivered_at_str: this._fmtDateTime(order.delivered_at),
        },
        items,
        steps: this._buildSteps(order),
      });
    } catch (e) {
      this.setData({ loading: false });
    }
  },

  _buildSteps(order) {
    const activeIndex = Math.max(0, STATUS_STEPS.findIndex(s => s.key === order.status));
    return STATUS_STEPS.map((step, index) => ({
      ...step,
      done: order.status === 'delivered' || index <= activeIndex,
      active: index === activeIndex && order.status !== 'delivered',
    }));
  },

  _fmtDateTime(s) {
    if (!s) return '';
    const d = new Date(s);
    const mm = `${d.getMonth() + 1}`.padStart(2, '0');
    const dd = `${d.getDate()}`.padStart(2, '0');
    const hh = `${d.getHours()}`.padStart(2, '0');
    const mi = `${d.getMinutes()}`.padStart(2, '0');
    return `${mm}.${dd} ${hh}:${mi}`;
  },

  onFeedback() {
    wx.switchTab({ url: '/pages/feedback/index' });
  },
});
