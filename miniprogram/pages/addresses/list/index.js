const api = require('../../../utils/api.js');
const auth = require('../../../utils/auth.js');

Page({
  data: {
    loading: true,
    addresses: [],
  },

  onShow() {
    if (!auth.isLoggedIn()) {
      auth.redirectToLogin();
      return;
    }
    this.fetch();
  },

  async fetch() {
    this.setData({ loading: true });
    try {
      const list = await api.listAddresses();
      this.setData({ addresses: list, loading: false });
    } catch (e) {
      this.setData({ loading: false });
    }
  },

  onAdd() {
    wx.navigateTo({ url: '/pages/addresses/edit/index' });
  },

  onEdit(e) {
    wx.navigateTo({ url: `/pages/addresses/edit/index?id=${e.currentTarget.dataset.id}` });
  },

  async onSetDefault(e) {
    await api.setDefaultAddress(e.currentTarget.dataset.id);
    this.fetch();
  },

  onDelete(e) {
    const id = e.currentTarget.dataset.id;
    wx.showModal({
      title: '删除这个地址？',
      content: '删除后如果是默认地址，会自动选另一个作为默认。',
      confirmColor: '#D95A4A',
      success: async (res) => {
        if (res.confirm) {
          await api.deleteAddress(id);
          this.fetch();
        }
      },
    });
  },
});
