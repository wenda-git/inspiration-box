/**
 * 登录态管理
 * - token 持久化到 wx.storage
 * - user 对象也缓存一份，冷启不等接口
 * - 给全局 globalData 用
 */

const TOKEN_KEY = 'vl_token';
const USER_KEY = 'vl_user';

function getToken() {
  try { return wx.getStorageSync(TOKEN_KEY) || ''; } catch (e) { return ''; }
}

function getUser() {
  try { return wx.getStorageSync(USER_KEY) || null; } catch (e) { return null; }
}

function setSession(token, user) {
  wx.setStorageSync(TOKEN_KEY, token);
  wx.setStorageSync(USER_KEY, user);
}

function clearSession() {
  wx.removeStorageSync(TOKEN_KEY);
  wx.removeStorageSync(USER_KEY);
}

function isLoggedIn() {
  return !!getToken();
}

/**
 * 跳登录页（把当前页面作为 redirect 参数）
 */
function redirectToLogin() {
  const pages = getCurrentPages();
  const cur = pages[pages.length - 1];
  const redirect = cur ? cur.route : '';
  wx.redirectTo({
    url: `/pages/login/index?redirect=${encodeURIComponent(redirect)}`,
  });
}

module.exports = {
  getToken,
  getUser,
  setSession,
  clearSession,
  isLoggedIn,
  redirectToLogin,
};
