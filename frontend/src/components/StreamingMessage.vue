<template>
  <div class="min-w-0">
    <!-- 流式等待中的占位（还没有任何 token） -->
    <div v-if="streaming && !text" class="flex items-center gap-1.5 py-1 text-ink-faint text-sm">
      <span class="typing-dot"></span>
      <span class="typing-dot" style="animation-delay: .15s"></span>
      <span class="typing-dot" style="animation-delay: .3s"></span>
    </div>
    <!-- Markdown 报告正文；「引自/出自…」渲染为可点击的原文检索入口 -->
    <div
      v-else
      class="prose-report text-sm"
      v-html="html"
      @click="onClick"
    ></div>

    <!-- 来源标签（从最终报告中提取的「…」引用） -->
    <div
      v-if="sources && sources.length"
      class="mt-3 pt-2 border-t border-line flex flex-wrap items-center gap-1.5"
    >
      <button
        v-for="s in sources"
        :key="s"
        class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-accent-soft text-accent text-xs hover:bg-accent hover:text-card transition-colors"
        :title="'检索原文：' + s"
        @click="emit('cite', s)"
      >📎 {{ s }}</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const props = defineProps({
  text: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
  sources: { type: Array, default: () => [] },
})
const emit = defineEmits(['cite'])

// html:false —— 原始 HTML 一律转义（与旧版 renderMarkdown 的转义行为一致）
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

// 仅「引自…/出自…」可点击，其余「」保持原样（旧版行为）
const CITE_RE = /「((?:引自|出自)[^」]+)」/g

const html = computed(() => {
  const rendered = md.render(props.text || '')
  return rendered.replace(
    CITE_RE,
    '<span class="cite" data-cite="$1">「$1」</span>',
  )
})

// 事件委托处理引用点击（v-html 内容无法直接绑事件）
function onClick(e) {
  const el = e.target.closest?.('[data-cite]')
  if (el) emit('cite', el.dataset.cite)
}
</script>

<style scoped>
/* v-html 注入的内容需要 :deep 才能命中 */
:deep(.cite) {
  color: #2f6f4f;
  cursor: pointer;
  text-decoration: underline dotted;
  text-underline-offset: 2px;
}
:deep(.cite:hover) {
  color: #265d41;
}

.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  background: #9b9a97;
  animation: typing-blink 1s infinite;
}
@keyframes typing-blink {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 1; }
}
</style>
