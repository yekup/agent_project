<template>
  <div class="p-6 max-w-4xl mx-auto">
    <h2 class="text-xl font-semibold text-ink mb-1">🔎 跨书检索</h2>
    <p class="text-sm text-ink-faint mb-5">在所有已编译书籍的章节摘要中同时搜索（多个关键词用空格分隔）</p>

    <!-- 搜索框 -->
    <div class="flex gap-2 mb-4">
      <input
        v-model="query"
        type="text"
        placeholder="输入人物名、事件、地点..."
        class="flex-1 px-4 py-2.5 rounded-lg bg-card border border-line text-ink outline-none focus:border-accent transition-colors"
        @keyup.enter="doSearch"
      />
      <button
        class="px-5 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors disabled:opacity-40"
        :disabled="searching || query.trim().length < 2"
        @click="doSearch"
      >{{ searching ? '搜索中...' : '搜索' }}</button>
    </div>

    <!-- 索引覆盖范围 -->
    <p v-if="books.length" class="text-xs text-ink-faint mb-4">
      索引覆盖：{{ books.map(shortBook).join('、') }}
    </p>

    <!-- 结果 -->
    <div v-if="searched">
      <p class="text-sm text-ink-soft mb-3">共 {{ results.length }} 条结果</p>
      <div v-if="results.length === 0" class="bg-card border border-line rounded-lg p-8 text-center text-ink-faint text-sm">
        没有找到匹配的内容，换个关键词试试（单个词至少 2 个字）
      </div>
      <div class="space-y-3">
        <div
          v-for="(r, i) in results"
          :key="i"
          class="bg-card border border-line rounded-lg p-4 hover:border-accent/40 transition-colors"
        >
          <div class="flex items-center gap-2 mb-1.5">
            <span class="text-xs px-2 py-0.5 rounded bg-accent-soft text-accent font-medium">{{ shortBook(r.book) }}</span>
            <span class="text-sm font-medium text-ink">{{ r.chapter_title || `第 ${r.chapter_index + 1} 章` }}</span>
          </div>
          <p class="text-sm text-ink-soft leading-relaxed" v-html="highlight(r.summary)"></p>
        </div>
      </div>
    </div>

    <!-- 空态 -->
    <div v-else class="text-center py-16 text-ink-faint">
      <span class="text-4xl block mb-3 opacity-30">🔎</span>
      <p class="text-sm">试试搜索一个人物名，看看他在哪些书里出现过</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { apiGet, apiPost } from '../api/client'

const query = ref('')
const results = ref([])
const books = ref([])
const searching = ref(false)
const searched = ref(false)

async function doSearch() {
  const q = query.value.trim()
  if (q.length < 2 || searching.value) return
  searching.value = true
  try {
    const d = await apiPost('/api/search/multi', { query: q, top_k: 20 })
    results.value = d.results || []
    searched.value = true
  } catch (e) {
    console.warn('[MultiSearch] 搜索失败:', e)
  } finally {
    searching.value = false
  }
}

// "斗破苍穹作者：天蚕土豆" → "斗破苍穹"
function shortBook(name) {
  return (name || '').split('作者：')[0] || name
}

// 关键词高亮（转义后替换，防 XSS）
function highlight(text) {
  if (!text) return ''
  let html = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  for (const w of query.value.trim().split(/[\s,，、]+/).filter((w) => w.length >= 2)) {
    const esc = w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    html = html.replace(new RegExp(esc, 'g'), (m) => `<mark class="bg-accent-soft text-accent rounded px-0.5">${m}</mark>`)
  }
  return html
}

onMounted(async () => {
  try {
    const d = await apiGet('/api/search/multi/books')
    const stats = d.books
    books.value = Array.isArray(stats) ? stats : Object.keys(stats || {})
  } catch { /* 书籍列表仅作展示，失败忽略 */ }
})
</script>
