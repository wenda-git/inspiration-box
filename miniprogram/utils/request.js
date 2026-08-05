/**
 * 统一请求器：
 * - 自动加 Authorization Bearer token
 * - 自动加 Idempotency-Key（POST/PUT/DELETE）
 * - 401 自动 clearSession + redirectToLogin
 * - 业务错误统一 toast
 */

const auth = require('./auth.js');

const app = getApp();

function handleResponse(res, { silent, resolve, reject }) {
  if (res.statusCode >= 200 && res.statusCode < 300) {
    resolve(res.data);
    return;
  }

  const detail = (res.data && res.data.detail) || {};
  const code = detail.code || `HTTP_${res.statusCode}`;
  const message = detail.message || res.errMsg || '请求失败';

  if (res.statusCode === 401) {
    auth.clearSession();
    reject({ code, message, status: 401 });
    return;
  }
  if (!silent) {
    wx.showToast({ title: message, icon: 'none' });
  }
  reject({ code, message, status: res.statusCode });
}

function handleNetworkError(err, { silent, reject }) {
  if (!silent) wx.showToast({ title: '网络异常', icon: 'none' });
  reject({
    code: 'NETWORK',
    message: (err && err.errMsg) || '网络异常',
  });
}

function uuid() {
  // 微信小程序里 crypto.getRandomValues 可用时用它；否则降级到 Math.random
  // 这个 key 会写进 payments.idempotency_key（UNIQUE），防碰撞重要
  try {
    const g = wx.getRandomValuesSync({ length: 16 }).randomValues;
    const bytes = new Uint8Array(g);
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant
    const hex = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  } catch (e) {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
}

function request({ url, method = 'GET', data, headers = {}, silent = false, timeout } = {}) {
  const h = { 'Content-Type': 'application/json', ...headers };
  const token = auth.getToken();
  if (token) h['Authorization'] = `Bearer ${token}`;
  if (['POST', 'PUT', 'DELETE'].includes(method) && !h['Idempotency-Key']) {
    h['Idempotency-Key'] = uuid();
  }

  return new Promise((resolve, reject) => {
    const callbacks = { silent, resolve, reject };

    if (app.globalData.useCloudContainer && !url.startsWith('http')) {
      if (!wx.cloud) {
        handleNetworkError(
          { errMsg: '当前基础库不支持 wx.cloud' },
          callbacks,
        );
        return;
      }
      wx.cloud.callContainer({
        config: { env: app.globalData.cloudEnvId },
        path: url,
        method,
        data,
        header: {
          ...h,
          'X-WX-SERVICE': app.globalData.cloudService,
        },
        success: (res) => handleResponse(res, callbacks),
        fail: (err) => handleNetworkError(err, callbacks),
      });
      return;
    }

    const fullUrl = url.startsWith('http') ? url : app.globalData.apiBase + url;
    wx.request({
      url: fullUrl,
      method,
      data,
      header: h,
      timeout,
      success: (res) => handleResponse(res, callbacks),
      fail: (err) => handleNetworkError(err, callbacks),
    });
  });
}

module.exports = { request };
