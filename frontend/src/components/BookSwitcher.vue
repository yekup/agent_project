<template>
  <div class="flex items-center gap-2">
    <span class="text-xs text-ink-faint">当前书籍</span>
    <select
      class="text-sm bg-paper border border-line rounded-md px-2.5 py-1.5 text-ink outline-none focus:border-accent transition-colors max-w-64"
      :value="novel.current"
      @change="onChange"
    >
      <option value="">默认（{{ defaultLabel }}）</option>
      <option v-for="n in novel.novels" :key="n.name" :value="n.name">
        {{ n.display_name }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { apiGet } from '../api/client'
import { useNovelStore } from '../stores/novel'

const novel = useNovelStore()

const defaultLabel = computed(
  () => novel.novels[0]?.display_name || '未编译书籍'
)

function onChange(e) {
  novel.setCurrent(e.target.value)
}

onMounted(async () => {
  try {
    novel.setNovels(await apiGet('/api/novels'))
  } catch (e) {
    console.warn('[BookSwitcher] 加载书籍列表失败:', e)
  }
})
</script>
