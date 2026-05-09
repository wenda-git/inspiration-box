const api = require('../../../utils/api.js');
const auth = require('../../../utils/auth.js');

const STATUS_LABEL = {
  locked: '已锁单',
  packing: '分拣中',
  shipped: '运输中',
  delivered: '已签收',
  cancelled: '已取消',
};
const PLAN_LABEL = {
  fatloss_gi: '减脂控糖',
  prenatal: '孕产营养',
  antiox: '抗氧轻食',
};

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    loading: true,
    orders: [],
  },

  async onShow() {
    if (!auth.isLoggedIn()) {
      auth.redirectToLogin();
      return;
    }
    this.setData({ loading: true });
    try {
      const raw = await api.listOrders(50);
      const orders = raw.map(o => ({
        ...o,
        status_label: STATUS_LABEL[o.status] || o.status,
        plan_label: PLAN_LABEL[o.plan_code] || o.plan_code,
        week_text: `${o.iso_year} 第 ${o.iso_week} 周`,
        items_short: (o.items_snapshot || []).slice(0, 3)
          .map(it => `${it.emoji || ''}${it.fruit_name_cn || ''}`).join(' · '),
        item_count: (o.items_snapshot || []).length,
        created_at_str: this._fmtDate(o.created_at),
      }));
      this.setData({ orders, loading: false });
    } catch (e) {
      this.setData({ loading: false });
    }
  },

  _fmtDate(s) {
    if (!s) return '';
    const d = new Date(s);
    return `${d.getFullYear()}.${(d.getMonth()+1+'').padStart(2,'0')}.${(d.getDate()+'').padStart(2,'0')}`;
  },

  onOrderTap(e) {
    const id = e.currentTarget.dataset.id;
    if (!id) return;
    wx.navigateTo({ url: `/pages/orders/detail/index?id=${id}` });
  },
});
