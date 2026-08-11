<template>
  <div class="px-8 py-8 max-w-5xl mx-auto">
    <!-- 概览统计 -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
      <div class="bg-card border border-line rounded-lg p-4">
        <div class="text-xs text-ink-faint mb-1">已入库书籍</div>
        <div class="text-2xl font-semibold text-ink">{{ novels.length }}</div>
      </div>
      <div class="bg-card border border-line rounded-lg p-4">
        <div class="text-xs text-ink-faint mb-1">缓存条目</div>
        <div class="text-2xl font-semibold text-ink">{{ cacheStat('entries') }}</div>
      </div>
      <div class="bg-card border border-line rounded-lg p-4">
        <div class="text-xs text-ink-faint mb-1">缓存命中率</div>
        <div class="text-2xl font-semibold text-ink">{{ hitRate }}</div>
      </div>
      <div class="bg-card border border-line rounded-lg p-4">
        <div class="text-xs text-ink-faint mb-1">缓存请求总数</div>
        <div class="text-2xl font-semibold text-ink">{{ cacheStat('total_requests') }}</div>
      </div>
    </div>

    <!-- 加载失败提示 -->
    <div
      v-if="loadError"
      class="bg-red-50 text-red-700 border border-red-200 p-3 rounded-md mb-6 text-sm"
    >{{ loadError }}</div>

    <!-- 书籍网格 -->
    <div v-if="novels.length" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <div
        v-for="n in novels"
        :key="n.name"
        class="bg-card border rounded-lg p-5 cursor-pointer transition-colors"
        :class="n.name === novel.current
          ? 'border-accent'
          : 'border-line hover:border-accent'"
        @click="openNovel(n)"
      >
        <div class="flex items-start justify-between gap-2 mb-3">
          <h3 class="text-base font-semibold text-ink leading-snug">{{ n.display_name }}</h3>
          <span
            v-if="n.name === novel.current"
            class="shrink-0 text-xs px-2 py-0.5 rounded-full bg-accent-soft text-accent"
          >当前</span>
        </div>
        <div class="flex gap-2 mb-4">
          <span
            class="text-xs px-2 py-0.5 rounded-full"
            :class="n.has_graph ? 'bg-accent-soft text-accent' : 'bg-paper text-ink-faint'"
          >{{ n.has_graph ? '✓ 图谱' : '✗ 图谱' }}</span>
          <span
            class="text-xs px-2 py-0.5 rounded-full"
            :class="n.has_wiki ? 'bg-accent-soft text-accent' : 'bg-paper text-ink-faint'"
          >{{ n.has_wiki ? '✓ Wiki' : '✗ Wiki' }}</span>
        </div>
        <div class="flex gap-2" @click.stop>
          <button
            class="text-xs px-3 py-1.5 rounded-md bg-accent text-white hover:bg-accent-hover transition-colors"
            @click="goChat(n)"
          >💬 问答</button>
          <button
            class="text-xs px-3 py-1.5 rounded-md border border-line text-ink-soft hover:bg-accent-soft hover:text-accent transition-colors"
            @click="goGraph(n)"
          >🕸 图谱</button>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div
      v-else-if="!loading"
      class="bg-card border border-line rounded-lg py-16 flex flex-col items-center text-center"
    >
      <div class="text-4xl mb-4">📚</div>
      <p class="text-ink-soft mb-1">书架还是空的</p>
      <p class="text-ink-faint text-sm mb-6">上传一本 TXT 小说，系统会自动解析并生成人物关系图谱</p>
      <button
        class="px-5 py-2.5 rounded-md bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors"
        @click="router.push('/upload')"
      >⬆️ 去上传第一本书</button>
    </div>

    <!-- 加载中 -->
    <div v-else class="text-center text-ink-faint text-sm py-16">加载中…</div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiGet } from '../api/client'
import { useNovelStore } from '../stores/novel'

const router = useRouter()
const novel = useNovelStore()

const loading = ref(true)
const loadError = ref('')
const cacheStats = ref(null)

const novels = computed(() => novel.novels)

function cacheStat(key) {
  const v = cacheStats.value?.[key]
  return typeof v === 'number' ? v : '—'
}

const hitRate = computed(() => {
  const v = cacheStats.value?.hit_rate
  return typeof v === 'number' ? v.toFixed(1) + '%' : '—'
})

function openNovel(n) {
  novel.setCurrent(n.name)
  router.push(n.has_graph ? '/graph' : '/chat')
}

function goChat(n) {
  novel.setCurrent(n.name)
  router.push('/chat')
}

function goGraph(n) {
  novel.setCurrent(n.name)
  router.push('/graph')
}

onMounted(async () => {
  try {
    novel.setNovels(await apiGet('/api/novels'))
  } catch (e) {
    loadError.value = '书籍列表加载失败: ' + e.message
  } finally {
    loading.value = false
  }
  // 缓存统计字段不确定，失败不影响主内容
  try {
    cacheStats.value = await apiGet('/api/cache/stats')
  } catch (e) {
    console.warn('[Dashboard] 缓存统计加载失败:', e)
  }
})
</script>
