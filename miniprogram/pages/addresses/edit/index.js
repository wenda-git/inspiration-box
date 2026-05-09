const api = require('../../../utils/api.js');

const PHONE_RE = /^1[3-9]\d{9}$/;

Page({
  data: {
    isNew: true,
    id: '',
    form: {
      receiver_name: '',
      receiver_phone: '',
      region: '',           // 展示字符串："广东省/广州市/越秀区"
      detail: '',
      is_default: false,
    },
    regionArr: [],          // picker 绑定用：['广东省','广州市','越秀区']
    submitting: false,
    canSubmit: false,
    redirect: '',
  },

  onLoad(query) {
    if (query.redirect) {
      this.setData({ redirect: decodeURIComponent(query.redirect) });
    }
    if (query.id) {
      this.setData({ isNew: false, id: query.id });
      this._load(query.id);
    }
  },

  async _load(id) {
    try {
      const list = await api.listAddresses();
      const found = list.find((a) => a.id === id);
      if (found) {
        const regionArr = (found.region || '').split('/').filter(Boolean);
        this.setData({ form: { ...found }, regionArr }, () => this._refresh());
      }
    } catch (e) {}
  },

  onInput(e) {
    const k = e.currentTarget.dataset.k;
    this.setData({ [`form.${k}`]: e.detail.value }, () => this._refresh());
  },

  onToggleDefault(e) {
    this.setData({ 'form.is_default': e.detail.value });
  },

  onRegionChange(e) {
    // e.detail.value = ['广东省','广州市','越秀区']（或两级：直辖市）
    const arr = e.detail.value || [];
    this.setData({
      regionArr: arr,
      'form.region': arr.join('/'),
    }, () => this._refresh());
  },

  // 微信地址簿 chooseAddress 已废弃（基础库 2.25+ 限制），改为手填
  // 如需后续支持，可接入 wx.getLocation + 腾讯地图逆地理编码

  _refresh() {
    const f = this.data.form;
    const detail = (f.detail || '').trim();
    const ok = f.receiver_name
      && PHONE_RE.test(f.receiver_phone)
      && f.region
      && detail.length >= 2;      // 详细地址至少 2 字（与后端 min_length=2 对齐）
    this.setData({ canSubmit: !!ok });
  },

  async onSubmit() {
    if (this.data.submitting) return;
    const f = this.data.form;
    if (!f.receiver_name) {
      wx.showToast({ title: '请填写收件人', icon: 'none' }); return;
    }
    if (!PHONE_RE.test(f.receiver_phone)) {
      wx.showToast({ title: '手机号格式不对', icon: 'none' }); return;
    }
    if (!f.region) {
      wx.showToast({ title: '请选择省市区', icon: 'none' }); return;
    }
    if ((f.detail || '').trim().length < 2) {
      wx.showToast({ title: '详细地址至少 2 个字', icon: 'none' }); return;
    }
    this.setData({ submitting: true });
    try {
      if (this.data.isNew) {
        await api.createAddress(this.data.form);
      } else {
        await api.updateAddress(this.data.id, this.data.form);
      }
      wx.showToast({ title: '已保存', icon: 'success' });
      setTimeout(() => {
        this.setData({ submitting: false });
        this._continue();
      }, 400);
    } catch (e) {
      this.setData({ submitting: false });
    }
  },

  async _continue() {
    if (this.data.redirect) {
      const target = this.data.redirect.startsWith('/') ? this.data.redirect : `/${this.data.redirect}`;
      const clean = target.replace(/^\//, '');
      const tabPages = new Set([
        'pages/weekly/index',
        'pages/feedback/index',
        'pages/profile/index',
      ]);
      if (tabPages.has(clean)) {
        wx.switchTab({ url: target });
      } else {
        wx.redirectTo({ url: target });
      }
      return;
    }

    // 根据 onboarding-status 决定下一站：
    // 新流程下订阅、画像已在前面完成，这里主要是首次进 weekly，或编辑地址后返回。
    try {
      const status = await api.getOnboardingStatus();
      if (status.next_step === 'weekly') {
        // 配置齐全 → 直接去 weekly tab（地址可能是从 profile 编辑进来的）
        wx.switchTab({ url: '/pages/weekly/index' });
        return;
      }
      // 还差步骤 → 交给 routeNext 决定
      const { routeNext } = require('../../../utils/flow.js');
      await routeNext();
    } catch (e) {
      // 兜底：有上一页就回去，没有就去 weekly
      const pages = getCurrentPages();
      if (pages.length > 1) {
        wx.navigateBack();
      } else {
        wx.switchTab({ url: '/pages/weekly/index' });
      }
    }
  },
});
