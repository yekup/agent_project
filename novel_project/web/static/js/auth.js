/**
 * 前端权限管理
 * =============
 * 页面加载时自动获取当前用户信息和权限列表，
 * 根据 data-perm 属性控制 DOM 元素的显隐。
 *
 * 用法:
 *   <a href="/upload" data-perm="page:upload">上传</a>
 *   <button data-perm="action:build">开始编译</button>
 *
 * 后端定义权限，前端根据后端返回的 permissions 决定显示什么。
 * 无权限的元素直接 display:none（而非 disabled）。
 */

const AUTH = {
  token: null,
  user: null,
  permissions: {},

  /** 初始化：从 localStorage 恢复 token，获取权限 */
  async init() {
    this.token = localStorage.getItem('token')
    this.user = JSON.parse(localStorage.getItem('user') || 'null')
    await this.fetchPermissions()
    this.applyPermissions()
  },

  /** 从后端获取当前用户和权限 */
  async fetchPermissions() {
    try {
      const resp = await fetch('/api/auth/me', {
        headers: this.token ? { Authorization: 'Bearer ' + this.token } : {},
      })
      if (resp.ok) {
        const data = await resp.json()
        this.user = data.user
        this.permissions = data.permissions || {}
        if (data.authenticated && data.user) {
          localStorage.setItem('user', JSON.stringify(data.user))
        } else if (!data.authenticated) {
          this.clear()
        }
      }
    } catch (e) {
      console.warn('[Auth] 获取权限失败:', e)
    }
  },

  /** 根据权限显隐元素 */
  applyPermissions() {
    // 1. 导航栏：根据 data-perm 显隐
    document.querySelectorAll('[data-perm]').forEach(el => {
      const required = el.dataset.perm
      if (!this.permissions[required]) {
        el.style.display = 'none'
      }
    })

    // 2. 用户信息显示
    const userInfoEl = document.getElementById('userInfo')
    if (userInfoEl && this.user) {
      userInfoEl.textContent = this.user.username + ' (' + this.user.role + ')'
    }

    // 3. 登录/登出按钮
    const loginBtn = document.getElementById('loginBtn')
    const logoutBtn = document.getElementById('logoutBtn')
    if (this.token) {
      if (loginBtn) loginBtn.style.display = 'none'
      if (logoutBtn) logoutBtn.style.display = 'inline'
    } else {
      if (loginBtn) loginBtn.style.display = 'inline'
      if (logoutBtn) logoutBtn.style.display = 'none'
    }
  },

  /** 登录成功保存 token */
  setToken(token, user) {
    this.token = token
    this.user = user
    localStorage.setItem('token', token)
    localStorage.setItem('user', JSON.stringify(user))
    this.fetchPermissions().then(() => this.applyPermissions())
  },

  /** 登出 */
  logout() {
    this.clear()
    window.location.href = '/login'
  },

  /** 清除登录状态 */
  clear() {
    this.token = null
    this.user = null
    this.permissions = {}
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  },

  /** 检查是否有某权限 */
  has(perm) {
    return !!this.permissions[perm]
  },
}

// 页面加载时自动初始化
document.addEventListener('DOMContentLoaded', () => AUTH.init())
