const { requestSmsCode, verifySmsCode } = require('../../utils/api.js');
const auth = require('../../utils/auth.js');

const PHONE_RE = /^1[3-9]\d{9}$/;

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    phone: '',
    code: '',
    codeSent: false,
    cooldown: 0,
    sending: false,          // 防连点：请求发起到返回之间锁住
    submitting: false,
    canLogin: false,
    redirect: '',
  },

  onLoad(query) {
    this.setData({ redirect: query.redirect ? decodeURIComponent(query.redirect) : '' });
  },

  onUnload() {
    if (this._timer) clearInterval(this._timer);
  },

  onPhoneInput(e) {
    this.setData({ phone: e.detail.value }, () => this._refreshCanLogin());
  },

  onCodeInput(e) {
    this.setData({ code: e.detail.value }, () => this._refreshCanLogin());
  },

  _refreshCanLogin() {
    const { phone, code } = this.data;
    this.setData({ canLogin: PHONE_RE.test(phone) && code.length === 6 });
  },

  async onSendCode() {
    if (this.data.cooldown > 0 || this.data.sending) return;
    if (!PHONE_RE.test(this.data.phone)) {
      wx.showToast({ title: '手机号格式不对', icon: 'none' });
      return;
    }
    this.setData({ sending: true });
    try {
      const res = await requestSmsCode(this.data.phone);
      this.setData({
        sending: false,
        codeSent: true,
        cooldown: res.cooldown_sec || 60,
      });
      this._timer = setInterval(() => {
        const left = this.data.cooldown - 1;
        if (left <= 0) {
          clearInterval(this._timer);
          this.setData({ cooldown: 0 });
        } else {
          this.setData({ cooldown: left });
        }
      }, 1000);
      wx.showToast({ title: '验证码已发送', icon: 'none' });
    } catch (e) {
      this.setData({ sending: false });
      // request.js 已 toast
    }
  },

  async onLogin() {
    if (!this.data.canLogin || this.data.submitting) return;
    this.setData({ submitting: true });
    try {
      const { token, user } = await verifySmsCode(this.data.phone, this.data.code);
      auth.setSession(token, user);
      getApp().globalData.token = token;
      getApp().globalData.user = user;

      wx.showToast({ title: '登录成功', icon: 'success' });
      setTimeout(() => this._afterLogin(), 500);
    } catch (e) {
      this.setData({ submitting: false });
    }
  },

  async _afterLogin() {
    const redirect = this.data.redirect;
    if (redirect) {
      const target = redirect.startsWith('/') ? redirect : `/${redirect}`;
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

    // 登录成功 → 按 onboarding 状态智能跳
    const { routeNext } = require('../../utils/flow.js');
    await routeNext();
  },

  onAgreement() {
    wx.showModal({
      title: '用户协议',
      content: '登录后，你可以创建健康画像、生成每周果箱、管理订阅与订单。请确保填写的信息真实有效；平台会按你的授权提供配单、配送和售后服务。',
      showCancel: false,
      confirmText: '我知道了',
    });
  },
  onPrivacy() {
    wx.showModal({
      title: '隐私政策',
      content: '我们会收集手机号、健康画像、收货地址、订单与反馈信息，用于登录、配单、配送、售后和服务改进。未经授权不会向无关第三方出售你的个人信息。',
      showCancel: false,
      confirmText: '我知道了',
    });
  },
});
