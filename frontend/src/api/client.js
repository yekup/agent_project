/**
 * API 封装（移植自旧版 static/js/auth.js 的 apiFetch）
 * - 自动附加 Authorization: Bearer <token>
 * - 401 时清除登录态并跳登录页
 * - FormData 上传不要手动设 Content-Type（浏览器自动带 boundary）
 */
import { useAuthStore } from '../stores/auth'
import router from '../router'

export async function apiFetch(url, options = {}) {
  const auth = useAuthStore()
  const headers = { ...(options.headers || {}) }
  if (auth.token) headers['Authorization'] = 'Bearer ' + auth.token

  const resp = await fetch(url, { ...options, headers })
  if (resp.status === 401) {
    auth.clear()
    router.push('/login')
    throw new Error('未登录或登录已过期')
  }
  return resp
}

export async function apiGet(url) {
  const resp = await apiFetch(url)
  if (!resp.ok) throw new Error(`请求失败: ${resp.status}`)
  return resp.json()
}

export async function apiPost(url, body) {
  const resp = await apiFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!resp.ok) {
    let detail = `请求失败: ${resp.status}`
    try { detail = (await resp.json()).detail || detail } catch { /* 保持默认 */ }
    throw new Error(detail)
  }
  return resp.json()
}
