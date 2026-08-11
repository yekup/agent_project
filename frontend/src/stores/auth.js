import { defineStore } from 'pinia'

/**
 * 鉴权状态（移植自旧版 auth.js）
 * token/user 持久化在 localStorage；permissions 由后端 /api/auth/me 下发，
 * 前端用 has(perm) 控制按钮/入口显隐（无权限直接不渲染）。
 */
export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: JSON.parse(localStorage.getItem('user') || 'null'),
    permissions: {},
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    has: (s) => (perm) => !!s.permissions[perm],
  },
  actions: {
    setToken(token, user) {
      this.token = token
      this.user = user
      localStorage.setItem('token', token)
      localStorage.setItem('user', JSON.stringify(user))
    },
    clear() {
      this.token = ''
      this.user = null
      this.permissions = {}
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    },
    async fetchPermissions() {
      try {
        const resp = await fetch('/api/auth/me', {
          headers: this.token ? { Authorization: 'Bearer ' + this.token } : {},
        })
        if (!resp.ok) return
        const data = await resp.json()
        this.permissions = data.permissions || {}
        if (data.authenticated && data.user) {
          this.user = data.user
          localStorage.setItem('user', JSON.stringify(data.user))
        } else if (!data.authenticated) {
          this.clear()
        }
      } catch (e) {
        console.warn('[Auth] 获取权限失败:', e)
      }
    },
  },
})
