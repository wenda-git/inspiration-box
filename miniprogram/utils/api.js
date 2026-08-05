const { request } = require('./request.js');
const auth = require('./auth.js');

// ---------- plans ----------
async function listPlans() {
  return request({ url: '/v1/plans' });
}

// ---------- auth ----------
async function requestSmsCode(phone) {
  return request({
    url: '/v1/auth/request-code',
    method: 'POST',
    data: { phone },
  });
}

async function verifySmsCode(phone, code) {
  return request({
    url: '/v1/auth/verify-code',
    method: 'POST',
    data: { phone, code },
  });
}

// ---------- addresses ----------
async function listAddresses() {
  return request({ url: '/v1/addresses' });
}
async function createAddress(payload) {
  return request({ url: '/v1/addresses', method: 'POST', data: payload });
}
async function updateAddress(id, payload) {
  return request({ url: `/v1/addresses/${id}`, method: 'PUT', data: payload });
}
async function deleteAddress(id) {
  return request({ url: `/v1/addresses/${id}`, method: 'DELETE' });
}
async function setDefaultAddress(id) {
  return request({ url: `/v1/addresses/${id}/default`, method: 'POST' });
}

// ---------- recommend / feedback ----------
async function getWeeklyRecommendation(refresh = false) {
  const url = refresh ? '/v1/recommend/current?refresh=true' : '/v1/recommend/current';
  // LLM 实时调用可能要 20s+，把 timeout 放宽
  return request({ url, timeout: 40000 });
}

async function submitFeedback(payload) {
  return request({ url: '/v1/feedback', method: 'POST', data: payload });
}

async function uploadFeedbackPhoto(filePath) {
  const app = getApp();

  // 云开发模式直接存云存储，避免为 multipart 上传额外开放公网接口。
  if (app.globalData.useCloudContainer) {
    const suffixMatch = String(filePath || '').match(/\.([a-zA-Z0-9]+)(?:\?.*)?$/);
    const suffix = suffixMatch ? suffixMatch[1].toLowerCase() : 'jpg';
    const cloudPath = `feedback/${Date.now()}-${Math.random().toString(16).slice(2)}.${suffix}`;
    try {
      const result = await wx.cloud.uploadFile({ cloudPath, filePath });
      return {
        url: result.fileID,
        fileID: result.fileID,
        content_type: `image/${suffix === 'jpg' ? 'jpeg' : suffix}`,
      };
    } catch (err) {
      wx.showToast({ title: '照片上传失败', icon: 'none' });
      throw {
        code: 'UPLOAD_FAILED',
        message: (err && err.errMsg) || '照片上传失败',
      };
    }
  }

  const token = auth.getToken();
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: app.globalData.apiBase + '/v1/uploads/feedback-photo',
      filePath,
      name: 'file',
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success(res) {
        let data = {};
        try {
          data = typeof res.data === 'string' ? JSON.parse(res.data || '{}') : (res.data || {});
        } catch (e) {
          data = {};
        }
        if (res.statusCode >= 200 && res.statusCode < 300 && data.url) {
          resolve(data);
          return;
        }
        const detail = data.detail || {};
        const message = detail.message || '照片上传失败';
        wx.showToast({ title: message, icon: 'none' });
        reject({ code: detail.code || `HTTP_${res.statusCode}`, message, status: res.statusCode });
      },
      fail(err) {
        wx.showToast({ title: '照片上传失败', icon: 'none' });
        reject({ code: 'UPLOAD_FAILED', message: err.errMsg });
      },
    });
  });
}

async function getFeedbackHistory(limit = 20) {
  return request({ url: `/v1/feedback/history?limit=${limit}` });
}

async function getFeedbackPreferences() {
  return request({ url: '/v1/feedback/preferences' });
}

// ---------- share reports ----------
async function createShareReport(payload, source = 'weekly') {
  return request({
    url: '/v1/share-reports',
    method: 'POST',
    data: { source, payload },
    silent: true,
  });
}

async function getShareReport(shareId) {
  return request({ url: `/v1/share-reports/${shareId}`, silent: true });
}

// ---------- orders ----------
async function getCurrentOrder() {
  return request({ url: '/v1/orders/current' });
}
async function getOrder(id) {
  return request({ url: `/v1/orders/${id}` });
}
async function listOrders(limit = 20) {
  return request({ url: `/v1/orders?limit=${limit}` });
}

// ---------- me ----------
async function getOnboardingStatus() {
  return request({ url: '/v1/me/onboarding-status' });
}

// ---------- profile ----------
async function getProfile() {
  return request({ url: '/v1/profile' });
}
async function saveProfile(payload) {
  return request({ url: '/v1/profile', method: 'POST', data: payload });
}

// ---------- subscriptions ----------
async function getCurrentSubscription() {
  return request({ url: '/v1/subscriptions/current' });
}
async function createSubscription(plan_code, address_id) {
  return request({
    url: '/v1/subscriptions',
    method: 'POST',
    data: { plan_code, address_id },
  });
}
async function cancelSubscription(id) {
  return request({ url: `/v1/subscriptions/${id}/cancel`, method: 'POST' });
}
async function pauseSubscription(id, weeks = 1, reason = '') {
  return request({ url: `/v1/subscriptions/${id}/pause`, method: 'POST', data: { weeks, reason } });
}
async function resumeSubscription(id) {
  return request({ url: `/v1/subscriptions/${id}/resume`, method: 'POST' });
}
async function changeSubscriptionPlan(id, plan_code) {
  return request({ url: `/v1/subscriptions/${id}/plan`, method: 'POST', data: { plan_code } });
}
async function changeSubscriptionAddress(id, address_id) {
  return request({ url: `/v1/subscriptions/${id}/address`, method: 'POST', data: { address_id } });
}
async function paySubscription(id) {
  return request({ url: `/v1/subscriptions/${id}/pay`, method: 'POST' });
}

module.exports = {
  listPlans,
  requestSmsCode,
  verifySmsCode,
  listAddresses,
  createAddress,
  updateAddress,
  deleteAddress,
  setDefaultAddress,
  getWeeklyRecommendation,
  submitFeedback,
  uploadFeedbackPhoto,
  getFeedbackHistory,
  getFeedbackPreferences,
  createShareReport,
  getShareReport,
  getCurrentOrder,
  getOrder,
  listOrders,
  getOnboardingStatus,
  getProfile,
  saveProfile,
  getCurrentSubscription,
  createSubscription,
  cancelSubscription,
  pauseSubscription,
  resumeSubscription,
  changeSubscriptionPlan,
  changeSubscriptionAddress,
  paySubscription,
};
