const api = require('../../utils/api.js');
const auth = require('../../utils/auth.js');

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    loading: true,
    ready: false,           // 有本周配单才能反馈
    guardMessage: '',       // 前置缺失时的引导文案
    guardCta: '',
    guardTarget: '',
    items: [],
    quickMoods: [
      { v: 'love', label: '喜欢', rating: 5, kind: 'preference', comment: '喜欢这个水果，下周可保留相近口味' },
      { v: 'ok', label: '一般', rating: 3, kind: 'taste', comment: '感觉一般，下周可以少放一点' },
      { v: 'avoid', label: '下周别放', rating: 1, kind: 'preference', comment: '下周别放这个水果' },
    ],
    issueTags: [
      { v: 'too_sweet', label: '太甜', kind: 'taste', comment: '太甜了，下周降低甜度' },
      { v: 'too_sour', label: '太酸', kind: 'taste', comment: '太酸了，下周减少酸口水果' },
      { v: 'not_fresh', label: '不新鲜', kind: 'freshness', comment: '新鲜度不够，下周注意批次' },
      { v: 'too_hard', label: '太生/太硬', kind: 'freshness', comment: '太生或太硬，下周注意熟度' },
      { v: 'too_much', label: '分量多', kind: 'preference', comment: '分量偏多，下周减少这个水果' },
      { v: 'too_little', label: '分量少', kind: 'preference', comment: '分量偏少，下周可多放一点' },
      { v: 'damaged', label: '破损', kind: 'damage', comment: '包装或水果有破损' },
    ],
    form: {
      order_id: '',
      fruit_code: '',
      rating: 0,
      kind: 'taste',
      comment: '',
      photo_urls: [],
      mood: '',
      issues: [],
    },
    selectedFruit: null,
    canSubmit: false,
    submitting: false,
    photoUploading: false,
    photoLocalPaths: [],
    feedbackOrderId: '',
  },

  async onShow() {
    const isLoggedIn = auth.isLoggedIn();
    if (!isLoggedIn) {
      this.setData({
        loading: false, ready: false,
        guardMessage: '登录后可对本周水果反馈',
        guardCta: '去登录',
        guardTarget: 'login',
      });
      return;
    }
    await this._checkAndLoad();
  },

  async _checkAndLoad() {
    this.setData({ loading: true });
    // 先看前置状态
    try {
      const status = await api.getOnboardingStatus();
      if (!status.has_profile) {
        this.setData({
          loading: false, ready: false,
          guardMessage: '还没有健康画像',
          guardCta: '去完成问卷',
          guardTarget: '/pages/onboarding/index',
        });
        return;
      }
      if (!status.has_subscription) {
        this.setData({
          loading: false, ready: false,
          guardMessage: '还没有订阅',
          guardCta: '去完善画像',
          guardTarget: '/pages/onboarding/index',
        });
        return;
      }
      if (!status.has_paid_subscription) {
        this.setData({
          loading: false, ready: false,
          guardMessage: '还没有完成首单支付',
          guardCta: '去看本周',
          guardTarget: 'tab:/pages/weekly/index',
        });
        return;
      }

      const orders = await api.listOrders(20).catch(() => []);
      const delivered = (orders || []).find(o => o.status === 'delivered');
      if (delivered) {
        const items = (delivered.items_snapshot || []).map(it => ({
          fruit_code: it.fruit_code,
          fruit_name_cn: it.fruit_name_cn || it.name || '本周水果',
          emoji: it.emoji || '🍎',
          qty_g: it.qty_g || it.qty || 0,
          reason: it.reason || it.tag || '',
        })).filter(it => it.fruit_code);
        if (items.length) {
          this.setData({
            loading: false,
            ready: true,
            items,
            feedbackOrderId: delivered.id,
          });
          return;
        }
      }

      const orderStatus = status.current_order_status;
      if (!orderStatus || orderStatus === 'cancelled') {
        this.setData({
          loading: false, ready: false,
          guardMessage: '还没有可反馈的已签收果箱',
          guardCta: '去确认配单',
          guardTarget: 'tab:/pages/weekly/index',
        });
        return;
      }
      if (orderStatus === 'locked' || orderStatus === 'packing' || orderStatus === 'shipped') {
        const hints = {
          locked:  '本周已锁单，周四发货。收到货之后再来反馈吧。',
          packing: '云仓正在为你分拣，收到货之后再来反馈。',
          shipped: '水果在路上了，收到货再来反馈口感。',
        };
        this.setData({
          loading: false, ready: false,
          guardMessage: hints[orderStatus],
          guardCta: '去看本周',
          guardTarget: 'tab:/pages/weekly/index',
        });
        return;
      }
    } catch (e) {
      this.setData({ loading: false });
      return;
    }

    // 兼容旧数据：当前周状态已签收但订单快照为空时，回退读本周配单。
    try {
      const data = await api.getWeeklyRecommendation();
      const items = (data && data.items) || [];
      if (!items.length) {
        this.setData({
          loading: false, ready: false,
          guardMessage: '本周还没生成配单',
          guardCta: '去看本周',
          guardTarget: 'tab:/pages/weekly/index',
        });
        return;
      }
      this.setData({ loading: false, ready: true, items, feedbackOrderId: '' });
    } catch (e) {
      this.setData({
        loading: false, ready: false,
        guardMessage: '暂时无法读取本周配单',
        guardCta: '去看本周',
        guardTarget: 'tab:/pages/weekly/index',
      });
    }
  },

  onGuardTap() {
    const t = this.data.guardTarget;
    if (!t) return;
    if (t === 'login') {
      wx.navigateTo({ url: '/pages/login/index' });
    } else if (t.startsWith('tab:')) {
      wx.switchTab({ url: t.slice(4) });
    } else {
      wx.redirectTo({ url: t });
    }
  },

  onTextareaInput(e) {
    this.setData({ 'form.comment': e.detail.value });
  },

  onPickFruit(e) {
    const code = e.currentTarget.dataset.v;
    const selectedFruit = this.data.items.find(it => it.fruit_code === code) || null;
    this.setData({
      selectedFruit,
      form: {
        order_id: this.data.feedbackOrderId || '',
        fruit_code: code,
        rating: 0,
        kind: 'taste',
        comment: '',
        photo_urls: [],
        mood: '',
        issues: [],
      },
      photoLocalPaths: [],
    });
    this.refreshSubmit();
  },

  onMood(e) {
    const v = e.currentTarget.dataset.v;
    const mood = this.data.quickMoods.find(x => x.v === v);
    if (!mood) return;
    this.setData({
      'form.mood': v,
      'form.rating': mood.rating,
      'form.kind': mood.kind,
    });
    this.refreshSubmit();
  },

  onToggleIssue(e) {
    const v = e.currentTarget.dataset.v;
    const arr = (this.data.form.issues || []).slice();
    const idx = arr.indexOf(v);
    if (idx >= 0) arr.splice(idx, 1);
    else arr.push(v);
    this.setData({ 'form.issues': arr });
    this.refreshSubmit();
  },

  onInput(e) {
    this.setData({ 'form.comment': e.detail.value });
  },

  onPhoto() {
    if (this.data.photoUploading) return;
    wx.chooseMedia({
      count: 3, mediaType: ['image'],
      success: async (res) => {
        const paths = (res.tempFiles || []).map(f => f.tempFilePath);
        if (!paths.length) return;
        this.setData({ photoUploading: true, photoLocalPaths: paths });
        wx.showLoading({ title: '上传照片…', mask: true });
        let uploadedCount = 0;
        try {
          const uploaded = [];
          for (const p of paths) {
            const ret = await api.uploadFeedbackPhoto(p);
            uploaded.push(ret.url);
          }
          uploadedCount = uploaded.length;
          this.setData({ 'form.photo_urls': uploaded });
        } catch (e) {
          this.setData({ 'form.photo_urls': [], photoLocalPaths: [] });
        } finally {
          wx.hideLoading();
          this.setData({ photoUploading: false });
          if (uploadedCount > 0) {
            wx.showToast({ title: `已上传 ${uploadedCount} 张`, icon: 'success' });
          }
        }
      },
    });
  },

  refreshSubmit() {
    const { fruit_code, mood, issues } = this.data.form;
    this.setData({ canSubmit: !!fruit_code && (!!mood || (issues || []).length > 0) });
  },

  _buildSubmitPayload() {
    const f = this.data.form;
    const mood = this.data.quickMoods.find(x => x.v === f.mood);
    const issues = (f.issues || [])
      .map(v => this.data.issueTags.find(x => x.v === v))
      .filter(Boolean);

    let rating = mood ? mood.rating : 3;
    if (issues.some(x => ['too_sweet', 'too_sour', 'not_fresh', 'too_hard', 'damaged'].includes(x.v))) {
      rating = Math.min(rating, 2);
    }
    if (issues.some(x => x.v === 'damaged')) {
      rating = Math.min(rating, 1);
    }

    const kind = issues.find(x => x.kind === 'damage')?.kind
      || issues.find(x => x.kind === 'freshness')?.kind
      || mood?.kind
      || issues[0]?.kind
      || 'taste';

    const comments = [];
    if (mood) comments.push(mood.comment);
    issues.forEach(x => comments.push(x.comment));
    if (f.comment) comments.push(f.comment);

    return {
      order_id: f.order_id || undefined,
      fruit_code: f.fruit_code,
      rating,
      kind,
      comment: comments.join('；') || null,
      photo_urls: f.photo_urls || [],
    };
  },

  async onSubmit() {
    if (!this.data.canSubmit || this.data.submitting) return;
    const payload = this._buildSubmitPayload();
    if (payload.kind === 'damage' && (!payload.photo_urls || payload.photo_urls.length === 0)) {
      wx.showToast({ title: '破损反馈需上传照片', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    try {
      const res = await api.submitFeedback(payload);
      wx.showModal({
        title: '反馈已记录',
        content: res && res.will_affect_next_week
          ? '下周配单会参考这条反馈，你会在本周页看到调整说明。'
          : '已收到你的反馈，我们会继续优化后续果箱。',
        confirmText: '看本周',
        cancelText: '继续反馈',
        success: (modalRes) => {
          this.setData({ submitting: false });
          if (modalRes.confirm) {
            wx.switchTab({ url: '/pages/weekly/index' });
          }
        },
      });
    } catch (e) {
      this.setData({ submitting: false });
    }
  },
});
