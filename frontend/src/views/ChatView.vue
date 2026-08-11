<template>
  <div class="flex h-full overflow-hidden">
    <!-- 左侧：会话列表 -->
    <aside class="w-52 shrink-0 bg-card border-r border-line flex flex-col">
      <div class="px-3 py-3 border-b border-line flex items-center justify-between">
        <span class="text-sm font-medium text-ink">💬 会话</span>
        <button
          class="w-6 h-6 rounded-md text-ink-soft hover:bg-accent-soft hover:text-accent transition-colors leading-none"
          title="新建会话"
          @click="newSession"
        >＋</button>
      </div>
      <div class="flex-1 overflow-y-auto p-2 space-y-0.5">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="group flex items-center gap-1.5 px-2 py-1.5 rounded-md cursor-pointer text-xs transition-colors"
          :class="s.id === currentSessionId
            ? 'bg-accent-soft text-accent font-medium'
            : 'text-ink-soft hover:bg-paper'"
          @click="switchSession(s.id)"
        >
          <span class="shrink-0">▸</span>
          <span class="truncate flex-1">{{ s.title }}</span>
          <span class="text-[10px] text-ink-faint shrink-0">{{ s.messages.length }}</span>
          <button
            class="shrink-0 text-ink-faint hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity leading-none"
            title="删除会话"
            @click.stop="deleteSession(s.id)"
          >×</button>
        </div>
      </div>
      <div class="p-2 border-t border-line">
        <button
          class="w-full px-2 py-1.5 rounded-md text-xs text-ink-faint hover:text-red-500 hover:bg-paper transition-colors"
          @click="clearAllSessions"
        >🗑 清空全部</button>
      </div>
    </aside>

    <!-- 中间：聊天区域 -->
    <section class="flex-1 flex flex-col min-w-0">
      <header class="h-12 shrink-0 px-5 border-b border-line bg-card flex items-center justify-between">
        <span class="text-sm font-medium text-ink truncate">{{ currentSession?.title || '新对话' }}</span>
        <span class="text-xs text-ink-faint shrink-0 ml-3">
          {{ currentSession ? currentSession.messages.length + ' 条消息' : '' }}
        </span>
      </header>

      <!-- 消息区 -->
      <div ref="chatEl" class="flex-1 overflow-y-auto px-6 py-5">
        <div class="max-w-3xl mx-auto space-y-4">
          <!-- 空会话：示例问题 -->
          <div v-if="!currentSession?.messages.length" class="pt-10 text-center">
            <p class="text-ink-soft text-sm mb-1">向当前书籍提问，Agent 会检索图谱与原文后生成带引用的报告。</p>
            <p class="text-ink-faint text-xs mb-6">点击下面的示例快速开始</p>
            <div class="flex flex-wrap justify-center gap-2">
              <button
                v-for="q in EXAMPLES"
                :key="q"
                class="px-3 py-1.5 rounded-full bg-card border border-line text-xs text-ink-soft hover:border-accent hover:text-accent transition-colors"
                @click="sendMessage(q)"
              >{{ q }}</button>
            </div>
          </div>

          <template v-for="(m, i) in currentSession?.messages || []" :key="i">
            <!-- 用户消息 -->
            <div v-if="m.role === 'user'" class="flex justify-end">
              <div class="max-w-[80%] px-4 py-2.5 rounded-xl bg-accent-soft border border-accent/15">
                <p class="text-sm text-ink leading-relaxed whitespace-pre-wrap">{{ m.text }}</p>
              </div>
            </div>
            <!-- 助手消息 -->
            <div v-else class="flex justify-start">
              <div class="max-w-[88%] w-fit min-w-0 px-4 py-3 rounded-xl bg-card border border-line">
                <StreamingMessage
                  :text="isLiveIndex(i) ? liveText : m.text"
                  :streaming="isLiveIndex(i)"
                  :sources="m.sources"
                  @cite="openChapter"
                />
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- 输入区 -->
      <footer class="shrink-0 border-t border-line bg-card px-6 py-3">
        <div class="max-w-3xl mx-auto">
          <!-- 进度提示 -->
          <div v-if="streaming" class="flex items-center gap-3 mb-2">
            <div class="flex-1 h-1 rounded-full bg-paper overflow-hidden">
              <div class="h-full w-1/3 rounded-full bg-accent progress-slide"></div>
            </div>
            <p class="text-xs text-ink-faint whitespace-nowrap">{{ progressText }}</p>
          </div>
          <div class="flex items-end gap-2">
            <textarea
              ref="inputEl"
              v-model="input"
              rows="1"
              placeholder="输入你的问题…（Enter 发送，Shift+Enter 换行）"
              :disabled="streaming"
              class="flex-1 resize-none px-3.5 py-2.5 rounded-lg bg-paper border border-line text-sm text-ink outline-none focus:border-accent transition-colors disabled:opacity-60 max-h-40"
              @keydown.enter.exact.prevent="sendMessage()"
              @input="autoGrow"
            ></textarea>
            <button
              v-if="!streaming"
              class="px-5 py-2.5 rounded-lg bg-accent text-card text-sm font-medium hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              :disabled="!input.trim()"
              @click="sendMessage()"
            >发送</button>
            <button
              v-else
              class="px-5 py-2.5 rounded-lg border border-line text-sm text-ink-soft hover:text-red-500 hover:border-red-300 transition-colors"
              @click="stopStreaming"
            >■ 停止</button>
          </div>
        </div>
      </footer>
    </section>

    <!-- 原文检索弹窗（点击「引自…」引用） -->
    <Teleport to="body">
      <div
        v-if="chapterModal"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
        @click="chapterModal = null"
      >
        <div
          class="bg-card border border-line rounded-xl w-[700px] max-w-[92vw] max-h-[80vh] flex flex-col shadow-xl"
          @click.stop
        >
          <div class="flex items-center justify-between px-5 py-3.5 border-b border-line">
            <h3 class="text-sm font-medium text-ink">
              📄 原文检索：<span class="text-ink-soft">{{ chapterModal.keyword }}</span>
            </h3>
            <button
              class="text-ink-faint hover:text-ink text-lg leading-none transition-colors"
              @click="chapterModal = null"
            >×</button>
          </div>
          <div class="flex-1 overflow-y-auto p-5 text-sm leading-relaxed">
            <div v-if="chapterModal.loading" class="text-center text-ink-faint py-10">查询中…</div>
            <div v-else-if="chapterModal.error" class="text-center text-ink-faint py-10">{{ chapterModal.error }}</div>
            <div v-else-if="!chapterModal.chapters.length" class="text-center text-ink-faint py-10">未找到匹配的原文内容。</div>
            <template v-else>
              <div
                v-for="(ch, i) in chapterModal.chapters"
                :key="i"
                class="mb-3 p-3.5 rounded-lg bg-paper border border-line"
              >
                <div class="font-medium text-accent mb-1">{{ ch.chapter_title }}</div>
                <div class="text-ink-soft whitespace-pre-wrap">{{ ch.snippet }}</div>
                <div class="text-ink-faint text-[10px] mt-1.5">{{ ch.text_length }} 字</div>
              </div>
            </template>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, ref } from 'vue'
import { apiFetch, apiGet } from '../api/client'
import { useNovelStore } from '../stores/novel'
import StreamingMessage from '../components/StreamingMessage.vue'

const novel = useNovelStore()

/* ================================================================
   会话管理（移植自旧版 chat.html，localStorage 持久化，沿用同一 key）
   ================================================================ */
const STORAGE_KEY = 'novelgraph_chat_sessions'

const sessions = ref([])
const currentSessionId = ref('')

const currentSession = computed(
  () => sessions.value.find((s) => s.id === currentSessionId.value) || null,
)

function loadSessions() {
  try {
    sessions.value = JSON.parse(localStorage.getItem(STORAGE_KEY)) || []
  } catch {
    sessions.value = []
  }
  if (!sessions.value.length) newSession()
  else switchSession(sessions.value[0].id)
}

function saveSessions() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.value))
  } catch { /* 存储满等异常忽略（旧版行为） */ }
}

function newSession() {
  const id = 's_' + Date.now()
  sessions.value.unshift({ id, title: '新对话', messages: [], created: Date.now() })
  switchSession(id)
}

function switchSession(id) {
  currentSessionId.value = id
  saveSessions()
  scrollToBottom()
}

function deleteSession(id) {
  sessions.value = sessions.value.filter((s) => s.id !== id)
  if (!sessions.value.length) newSession()
  else if (id === currentSessionId.value) switchSession(sessions.value[0].id)
  saveSessions()
}

function clearAllSessions() {
  if (!window.confirm('确定清空所有会话？')) return
  sessions.value = []
  newSession()
}

/* ================================================================
   流式问答（POST /api/ask/stream，fetch + reader 手动解析 SSE）
   ================================================================ */
const input = ref('')
const inputEl = ref(null)
const chatEl = ref(null)

const streaming = ref(false)
const progressText = ref('')
// 正在流式生成的会话与实时文本（节流刷新到 UI）
const streamSessionId = ref('')
const liveText = ref('')
let accText = '' // 完整累积文本（非响应式，避免每个 token 触发渲染）
let abortCtrl = null
let flushTimer = null

const EXAMPLES = [
  '赵玖是什么角色？',
  '赵玖和岳飞是什么关系？',
  '韩世忠的主要事迹',
  '全书的核心主题是什么？',
  '赵玖在八公山上做了什么？',
]

function lastAssistantMsg(session) {
  const msgs = session?.messages
  if (!msgs?.length) return null
  const last = msgs[msgs.length - 1]
  return last.role === 'assistant' ? last : null
}

function isLiveIndex(i) {
  const msgs = currentSession.value?.messages
  return (
    streaming.value &&
    currentSessionId.value === streamSessionId.value &&
    msgs && i === msgs.length - 1 &&
    msgs[i].role === 'assistant'
  )
}

/* token 渲染节流：150ms 内积累的 token 合并成一次渲染（旧版行为） */
function scheduleFlush() {
  if (flushTimer) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    liveText.value = accText
    const msg = lastAssistantMsg(currentSession.value)
    if (msg && currentSessionId.value === streamSessionId.value) msg.text = accText
    scrollToBottom()
  }, 150)
}

function flushNow() {
  if (flushTimer) {
    clearTimeout(flushTimer)
    flushTimer = null
  }
  liveText.value = accText
}

function extractSources(report) {
  // 从最终报告中提取「…」作为来源标签（去重，旧版行为）
  const sources = []
  const matches = report.match(/「[^」]+」/g)
  if (matches) {
    for (const s of matches) {
      const clean = s.replace(/[「」]/g, '')
      if (!sources.includes(clean)) sources.push(clean)
    }
  }
  return sources
}

async function sendMessage(preset) {
  const q = (typeof preset === 'string' ? preset : input.value).trim()
  if (!q || streaming.value) return
  if (!currentSession.value) newSession()
  const session = currentSession.value
  input.value = ''
  resetInputHeight()

  session.messages.push({ role: 'user', text: q, sources: [], timestamp: Date.now() })
  // 首条问题作为会话标题（旧版行为）
  if (session.messages.length === 1) {
    session.title = q.length > 20 ? q.slice(0, 18) + '…' : q
  }
  session.messages.push({ role: 'assistant', text: '', sources: [], timestamp: Date.now() })
  saveSessions()
  scrollToBottom()

  streaming.value = true
  streamSessionId.value = session.id
  progressText.value = '分析中…'
  accText = ''
  liveText.value = ''
  abortCtrl = new AbortController()

  try {
    const resp = await apiFetch('/api/ask/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q, session_id: session.id, novel: novel.current }),
      signal: abortCtrl.signal,
    })
    if (!resp.ok) throw new Error('请求失败: ' + resp.status)

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      // SSE 帧: data: {json}\n\n
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''

      for (const part of parts) {
        for (const line of part.split('\n')) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6).trim()
          if (data === '[DONE]') return
          let ev
          try {
            ev = JSON.parse(data)
          } catch {
            continue
          }
          handleEvent(ev, session)
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      // 用户停止：保留已生成的部分文本（旧版 _streamingAbort 语义）
      const msg = lastAssistantMsg(session)
      if (msg && !msg.text) msg.text = accText || '（已停止生成）'
    } else {
      const msg = lastAssistantMsg(session)
      if (msg) msg.text = '网络错误: ' + (e.message || '未知错误')
    }
  } finally {
    finishStream()
  }
}

function handleEvent(ev, session) {
  const msg = lastAssistantMsg(session)
  switch (ev.event) {
    case 'start':
      progressText.value = ev.message || '开始分析…'
      break
    case 'progress':
      progressText.value = ev.message || '处理中…'
      break
    case 'draft_start':
      // 新一轮草稿（审核未通过后的重写），清空重攒
      accText = ''
      flushNow()
      if (msg) msg.text = ''
      break
    case 'token':
      accText += ev.text || ''
      scheduleFlush()
      break
    case 'error':
      accText = '分析失败: ' + (ev.message || '未知错误')
      flushNow()
      if (msg) msg.text = accText
      saveSessions()
      break
    case 'result': {
      // 以最终校验后的报告为准（引用校验可能修改流式草稿）
      const report = ev.report || ''
      accText = report
      flushNow()
      if (msg) {
        msg.text = report
        msg.sources = extractSources(report)
      }
      saveSessions()
      scrollToBottom()
      break
    }
  }
}

function finishStream() {
  flushNow()
  streaming.value = false
  progressText.value = ''
  abortCtrl = null
  saveSessions()
  scrollToBottom()
}

function stopStreaming() {
  abortCtrl?.abort()
}

/* ================================================================
   原文检索弹窗（点击「引自…」引用，GET /api/chapter）
   ================================================================ */
const chapterModal = ref(null)

async function openChapter(keyword) {
  chapterModal.value = { keyword, loading: true, chapters: [], error: '' }
  try {
    const data = await apiGet(
      '/api/chapter?keyword=' + encodeURIComponent(keyword) +
      '&novel=' + encodeURIComponent(novel.current),
    )
    if (chapterModal.value?.keyword === keyword) {
      chapterModal.value = { keyword, loading: false, chapters: data.chapters || [], error: '' }
    }
  } catch {
    if (chapterModal.value?.keyword === keyword) {
      chapterModal.value = { keyword, loading: false, chapters: [], error: '查询失败。' }
    }
  }
}

/* ================================================================
   UI 辅助
   ================================================================ */
function scrollToBottom() {
  nextTick(() => {
    const el = chatEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function resetInputHeight() {
  const el = inputEl.value
  if (el) el.style.height = 'auto'
}

onMounted(loadSessions)

onBeforeUnmount(() => {
  abortCtrl?.abort()
  if (flushTimer) clearTimeout(flushTimer)
})
</script>

<style scoped>
.progress-slide {
  animation: progress-slide 1.2s ease-in-out infinite;
}
@keyframes progress-slide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}
</style>
