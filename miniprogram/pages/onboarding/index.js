const api = require('../../utils/api.js');
const { visibleQuestions, buildDiagnosis } = require('./questions.js');
const auth = require('../../utils/auth.js');

const DRAFT_KEY = 'ib_onboarding_draft';

Page({
  data: {
    assetBase: getApp().globalData.assetBase,
    step: 0,
    total: 0,
    progress: 0,
    currentQ: null,
    form: {
      gender: '',
      height_cm: '',
      weight_kg: '',
      direction: '',              // 问卷里选的方向 → 决定后端 subscription.plan_code
      prenatal_stage: '',
      symptoms: [],
      veg_freq: '',
      sleep_pattern: '',
      exercise_freq: '',
      taste_prefer: [],
      has_allergies: '',
      allergies: [],
    },
    selectedMap: {},
    canSubmitNum: false,
    weightJin: '',
    // 诊断页
    showDiagnosis: false,
    diagnosis: null,
    submitting: false,
    // why 弹窗
    showWhy: false,
  },

  async onLoad(query = {}) {
    const draft = wx.getStorageSync(DRAFT_KEY);
    if (draft && query.resume) {
      const diag = buildDiagnosis(draft);
      this.setData({
        form: { ...this.data.form, ...draft },
        showDiagnosis: true,
        diagnosis: diag,
        progress: 100,
      }, () => {
        if (query.auto === '1') {
          setTimeout(() => this.onConfirmDiagnosis(), 80);
        }
      });
      return;
    }

    // 如果已有订阅，预填 direction，方便用户"重新做一次问卷"时保留选择
    if (auth.isLoggedIn()) {
      try {
        const sub = await api.getCurrentSubscription();
        if (sub && sub.plan_code) {
          this.setData({ 'form.direction': sub.plan_code });
        }
      } catch (e) {}
    }
    this._goTo(0);
  },

  _goTo(step) {
    const vq = visibleQuestions(this.data.form);
    const total = vq.length;
    if (step >= total) {
      const diag = buildDiagnosis(this.data.form);
      this.setData({
        showDiagnosis: true,
        diagnosis: diag,
        progress: 100,
      });
      return;
    }
    const q = vq[step];
    const val = this.data.form[q.key];
    const selectedMap = {};
    if (q.type === 'multi' || q.type === 'multi_grouped') {
      (val || []).forEach(v => { selectedMap[v] = true; });
    } else if (q.type === 'single' || q.type === 'single_circle') {
      if (val) selectedMap[val] = true;
    }
    const isNum = q.type === 'number';
    this.setData({
      step, total,
      currentQ: q,
      progress: Math.round(((step + 1) / total) * 100),
      showDiagnosis: false,
      selectedMap,
      canSubmitNum: isNum ? this._numValid(val, q) : false,
      weightJin: isNum && q.hint_jin && val ? this._toJin(val) : '',
      showWhy: false,
    });
  },

  _numValid(v, q) {
    const n = Number(v);
    if (v === '' || v === null || v === undefined || isNaN(n)) return false;
    if (q.min !== undefined && n < q.min) return false;
    if (q.max !== undefined && n > q.max) return false;
    return true;
  },

  _toJin(kg) {
    const n = Number(kg);
    if (!n || isNaN(n)) return '';
    return `= ${+(n * 2).toFixed(1)} 斤`;
  },

  onInputNumber(e) {
    const v = e.detail.value;
    const q = this.data.currentQ;
    this.setData({
      [`form.${q.key}`]: v,
      canSubmitNum: this._numValid(v, q),
      weightJin: q.hint_jin ? this._toJin(v) : '',
    });
  },

  onConfirmNumber() {
    const q = this.data.currentQ;
    if (!this.data.canSubmitNum) {
      wx.showToast({ title: `请填 ${q.min}-${q.max} ${q.unit}`, icon: 'none' });
      return;
    }
    this._goTo(this.data.step + 1);
  },

  onPickSingle(e) {
    const v = e.currentTarget.dataset.value;
    const key = this.data.currentQ.key;
    this.setData({
      [`form.${key}`]: v,
      selectedMap: { [v]: true },
    });
    setTimeout(() => this._goTo(this.data.step + 1), 240);
  },

  onToggleMulti(e) {
    const v = e.currentTarget.dataset.value;
    const q = this.data.currentQ;
    const key = q.key;
    const arr = (this.data.form[key] || []).slice();
    const idx = arr.indexOf(v);
    if (idx >= 0) {
      arr.splice(idx, 1);
    } else {
      if (v === 'none') {
        const selectedMap = { none: true };
        this.setData({ [`form.${key}`]: ['none'], selectedMap });
        return;
      }
      const noneIdx = arr.indexOf('none');
      if (noneIdx >= 0) arr.splice(noneIdx, 1);
      if (q.max && arr.length >= q.max) {
        wx.showToast({ title: `最多选 ${q.max} 项`, icon: 'none' });
        return;
      }
      arr.push(v);
    }
    const selectedMap = {};
    arr.forEach(x => { selectedMap[x] = true; });
    this.setData({ [`form.${key}`]: arr, selectedMap });
  },

  onNext() {
    const q = this.data.currentQ;
    const val = this.data.form[q.key];
    if ((q.type === 'multi' || q.type === 'multi_grouped') && (!val || val.length === 0)) {
      wx.showToast({ title: '至少选一项', icon: 'none' }); return;
    }
    this._goTo(this.data.step + 1);
  },

  onBack() {
    if (this.data.showDiagnosis) {
      const total = visibleQuestions(this.data.form).length;
      this._goTo(Math.max(0, total - 1));
      return;
    }
    if (this.data.step > 0) {
      this._goTo(this.data.step - 1);
      return;
    }
    const pages = getCurrentPages();
    if (pages.length > 1) {
      wx.navigateBack();
    } else {
      wx.switchTab({ url: '/pages/weekly/index' });
    }
  },

  onShowWhy() { this.setData({ showWhy: true }); },
  onHideWhy() { this.setData({ showWhy: false }); },

  async onConfirmDiagnosis() {
    if (this.data.submitting) return;

    if (!auth.isLoggedIn()) {
      wx.setStorageSync(DRAFT_KEY, this.data.form);
      wx.navigateTo({
        url: '/pages/login/index?redirect=' + encodeURIComponent('pages/onboarding/index?resume=1&auto=1'),
      });
      return;
    }

    this.setData({ submitting: true });
    wx.showLoading({ title: '生成果箱…', mask: true });

    const f = this.data.form;
    const direction = f.direction || 'antiox';

    // 给 selector 用的 goals：方向对应的主 goal + 选中的症状标签
    const goalForDir = {
      fatloss_gi: 'weight_loss',
      prenatal: 'prenatal',
      antiox: 'anti_aging',
    }[direction] || 'general_wellness';
    const goals = [goalForDir, ...(f.symptoms || []).filter(s => s !== 'none').slice(0, 4)];

    const payload = {
      gender: f.gender || 'other',
      height_cm: Number(f.height_cm),
      weight_kg: Number(f.weight_kg),
      goals,
      allergies: (f.allergies || []).filter(a => a !== 'none'),
      dislikes: [],
      prenatal_stage: f.prenatal_stage || null,
      veg_freq: f.veg_freq || null,
      sleep_pattern: f.sleep_pattern || null,
      exercise_freq: f.exercise_freq || null,
      taste_prefer: f.taste_prefer || [],
    };

    try {
      // 1. 保存画像
      await api.saveProfile(payload);
      // 2. 创建/更新订阅（C 方案：方向在问卷里选）
      await api.createSubscription(direction);
      wx.removeStorageSync(DRAFT_KEY);

      wx.hideLoading();
      wx.showToast({ title: '果箱已生成', icon: 'success' });

      setTimeout(() => {
        this.setData({ submitting: false });
        wx.redirectTo({ url: '/pages/weekly/detail/index' });
      }, 600);
    } catch (e) {
      wx.hideLoading();
      this.setData({ submitting: false });
      if (e && e.status === 401) {
        wx.setStorageSync(DRAFT_KEY, this.data.form);
        wx.showModal({
          title: '需要重新登录',
          content: '刚刚清理了测试用户数据，你当前登录态已失效。重新登录后会继续使用这份画像草稿。',
          showCancel: false,
          confirmText: '去登录',
          success: () => {
            const auth = require('../../utils/auth.js');
            auth.redirectToLogin();
          },
        });
      }
    }
  },
});
