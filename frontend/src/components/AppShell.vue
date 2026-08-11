<template>
  <div class="flex h-screen bg-paper">
    <!-- 侧栏 -->
    <aside class="w-56 shrink-0 bg-card border-r border-line flex flex-col">
      <div class="px-5 py-5 border-b border-line">
        <h1 class="text-lg font-semibold text-ink">NovelGraph</h1>
        <p class="text-xs text-ink-faint mt-0.5">网文知识图谱分析</p>
      </div>

      <nav class="flex-1 px-3 py-4 space-y-1">
        <RouterLink
          v-for="item in visibleNav"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-3 px-3 py-2 rounded-md text-sm text-ink-soft hover:bg-accent-soft hover:text-accent transition-colors"
          active-class="!bg-accent-soft !text-accent font-medium"
        >
          <span class="text-base leading-none">{{ item.icon }}</span>
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="px-4 py-4 border-t border-line">
        <div class="text-sm text-ink">{{ auth.user?.username }}</div>
        <div class="text-xs text-ink-faint">{{ auth.user?.role }}</div>
        <button
          class="mt-2 text-xs text-ink-faint hover:text-accent transition-colors"
          @click="logout"
        >退出登录</button>
      </div>
    </aside>

    <!-- 主区域 -->
    <div class="flex-1 flex flex-col min-w-0">
      <header class="h-14 shrink-0 bg-card border-b border-line flex items-center justify-between px-6">
        <BookSwitcher />
        <div class="text-xs text-ink-faint">{{ routeTitle }}</div>
      </header>
      <main class="flex-1 overflow-y-auto">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import BookSwitcher from './BookSwitcher.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const NAV = [
  { to: '/', label: '书架总览', icon: '📚', perm: '' },
  { to: '/chat', label: '智能问答', icon: '💬', perm: '' },
  { to: '/graph', label: '人物图谱', icon: '🕸', perm: '' },
  { to: '/search', label: '跨书检索', icon: '🔎', perm: '' },
  { to: '/upload', label: '上传编译', icon: '⬆️', perm: 'page:upload' },
]

// 无权限的入口直接不渲染（沿用旧版 data-perm 语义）
const visibleNav = computed(() => NAV.filter((n) => !n.perm || auth.has(n.perm)))

const routeTitle = computed(() => NAV.find((n) => n.to === route.path)?.label || '')

function logout() {
  auth.clear()
  router.push('/login')
}

onMounted(() => auth.fetchPermissions())
</script>
