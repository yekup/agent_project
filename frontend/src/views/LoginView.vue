<template>
  <div class="min-h-screen bg-paper flex items-center justify-center px-4">
    <div class="w-full max-w-sm bg-card border border-line rounded-xl p-8">
      <div class="text-center mb-6">
        <div class="text-4xl mb-3">🕸</div>
        <h1 class="text-2xl font-semibold text-ink">NovelGraph</h1>
        <p class="text-ink-faint text-sm mt-1">请登录以继续</p>
      </div>

      <div
        v-if="errorMsg"
        class="bg-red-50 text-red-700 border border-red-200 p-3 rounded-md mb-4 text-sm"
      >{{ errorMsg }}</div>

      <form class="space-y-4" @submit.prevent="onSubmit">
        <div>
          <label class="block text-sm text-ink-soft mb-1" for="username">用户名</label>
          <input
            id="username"
            v-model.trim="username"
            type="text"
            autocomplete="username"
            required
            class="w-full px-4 py-2.5 rounded-md bg-paper border border-line text-ink outline-none focus:border-accent transition-colors"
          />
        </div>
        <div>
          <label class="block text-sm text-ink-soft mb-1" for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            class="w-full px-4 py-2.5 rounded-md bg-paper border border-line text-ink outline-none focus:border-accent transition-colors"
          />
        </div>
        <button
          type="submit"
          :disabled="submitting"
          class="w-full px-4 py-2.5 rounded-md bg-accent text-white font-medium hover:bg-accent-hover transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {{ submitting ? '登录中…' : '登录' }}
        </button>
      </form>

      <p class="text-ink-faint text-xs text-center mt-4">默认管理员: admin / admin123</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const errorMsg = ref('')
const submitting = ref(false)

// 登录失败后端返回 401，若走 apiFetch 会被全局 401 处理拦截、
// 丢失真实的 detail（如「用户名或密码错误」），因此这里直接用原生 fetch（沿用旧模板行为）。
async function onSubmit() {
  errorMsg.value = ''
  submitting.value = true
  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value, password: password.value }),
    })
    const data = await resp.json()
    if (resp.ok) {
      auth.setToken(data.token, data.user)
      router.push(route.query.redirect || '/')
    } else {
      errorMsg.value = data.detail || '登录失败'
    }
  } catch (err) {
    errorMsg.value = '网络错误: ' + err.message
  } finally {
    submitting.value = false
  }
}
</script>
