<template>
  <div class="max-w-3xl mx-auto px-6 py-10">
    <h2 class="text-2xl font-semibold text-ink mb-2">上传编译</h2>
    <p class="text-sm text-ink-soft mb-8">上传小说文档，自动清洗、分块、编译，生成结构化知识图谱</p>

    <!-- 无权限提示 -->
    <div v-if="!auth.has('page:upload')" class="bg-card border border-line rounded-xl p-10 text-center">
      <div class="text-4xl mb-3">🔒</div>
      <p class="text-ink font-medium mb-1">没有上传权限</p>
      <p class="text-sm text-ink-faint">当前账号缺少 page:upload 权限，请联系管理员开通</p>
    </div>

    <template v-else>
      <!-- 第一步：选择文件 -->
      <section class="bg-card border border-line rounded-xl p-6 mb-5">
        <h3 class="text-base font-semibold text-ink mb-1">📄 选择文件</h3>
        <p class="text-xs text-ink-faint mb-4">支持格式：TXT / Word (.docx) / PDF / Markdown</p>
        <div
          class="border-2 border-dashed border-line rounded-lg py-10 text-center cursor-pointer transition-colors hover:border-accent hover:bg-accent-soft/40"
          @click="fileInput?.click()"
        >
          <div class="text-4xl mb-3">⬆️</div>
          <p class="text-sm text-ink-soft">{{ selectedFile ? selectedFile.name : '点击选择文件' }}</p>
          <input
            ref="fileInput"
            type="file"
            accept=".txt,.docx,.doc,.pdf,.md"
            class="hidden"
            @change="onFileSelect"
          />
        </div>
        <div class="flex items-center gap-3 mt-5">
          <button
            class="px-5 py-2 rounded-md bg-accent text-white text-sm font-medium transition-colors hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="!selectedFile || uploading"
            @click="uploadFile"
          >{{ uploading ? '上传并解析中...' : '上传并解析' }}</button>
          <span v-if="uploadMessage" class="text-sm" :class="uploadError ? 'text-red-600' : 'text-accent'">
            {{ uploadMessage }}
          </span>
        </div>
      </section>

      <!-- 解析结果 -->
      <section v-if="parseResult" class="bg-card border border-line rounded-xl p-6 mb-5">
        <h3 class="text-base font-semibold text-ink mb-4">✅ 解析结果</h3>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div class="border border-line rounded-lg p-4 text-center">
            <div class="text-2xl font-semibold text-accent">{{ parseResult.total_chapters }}</div>
            <div class="text-xs text-ink-faint mt-1">章节</div>
          </div>
          <div class="border border-line rounded-lg p-4 text-center">
            <div class="text-2xl font-semibold text-accent">{{ parseResult.total_chunks || '-' }}</div>
            <div class="text-xs text-ink-faint mt-1">语义块</div>
          </div>
          <div class="border border-line rounded-lg p-4 text-center">
            <div class="text-2xl font-semibold text-ink">{{ parseResult.format || 'txt' }}</div>
            <div class="text-xs text-ink-faint mt-1">格式</div>
          </div>
          <div class="border border-line rounded-lg p-4 text-center">
            <div class="text-2xl font-semibold text-ink">{{ fileSizeMb }}MB</div>
            <div class="text-xs text-ink-faint mt-1">大小</div>
          </div>
        </div>
      </section>

      <!-- 第二步：选择编译范围 -->
      <section v-if="parseResult && !buildComplete" class="bg-card border border-line rounded-xl p-6 mb-5">
        <h3 class="text-base font-semibold text-ink mb-1">⚙️ 选择编译章节</h3>
        <p class="text-xs text-ink-faint mb-4">共 <span class="text-ink font-medium">{{ parseResult.total_chapters }}</span> 章</p>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="border border-line rounded-lg p-5">
            <div class="font-medium text-ink mb-1">全部编译</div>
            <p class="text-xs text-ink-faint mb-4">完整编译全书所有章节</p>
            <button
              class="w-full px-4 py-2 rounded-md bg-accent text-white text-sm font-medium transition-colors hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed"
              :disabled="building"
              @click="startBuild({ chapters: -1 })"
            >▶ 编译全部</button>
          </div>
          <div class="border border-line rounded-lg p-5">
            <div class="font-medium text-ink mb-1">范围编译</div>
            <p class="text-xs text-ink-faint mb-4">指定起始和结束章节，只编译中间部分</p>
            <div class="flex items-center gap-2 mb-3">
              <input
                v-model.number="rangeStart"
                type="number"
                min="1"
                placeholder="起始章"
                class="w-1/2 px-3 py-2 rounded-md bg-paper border border-line text-ink text-sm focus:outline-none focus:border-accent"
              />
              <span class="text-ink-faint">至</span>
              <input
                v-model.number="rangeEnd"
                type="number"
                min="1"
                placeholder="结束章"
                class="w-1/2 px-3 py-2 rounded-md bg-paper border border-line text-ink text-sm focus:outline-none focus:border-accent"
              />
            </div>
            <button
              class="w-full px-4 py-2 rounded-md border border-accent text-accent text-sm font-medium transition-colors hover:bg-accent-soft disabled:opacity-50 disabled:cursor-not-allowed"
              :disabled="building"
              @click="startRangeBuild"
            >开始编译</button>
          </div>
        </div>
      </section>

      <!-- 第三步：编译进度 -->
      <section v-if="buildStarted" class="bg-card border border-line rounded-xl p-6 mb-5">
        <h3 class="text-base font-semibold text-ink mb-4">
          <span v-if="paused">⏸️</span>
          <span v-else-if="buildComplete">✅</span>
          <span v-else>⏳</span>
          编译进度
        </h3>
        <div class="w-full bg-paper border border-line rounded-full h-2.5 mb-3">
          <div
            class="bg-accent h-2.5 rounded-full transition-all duration-500"
            :style="{ width: progressPct + '%' }"
          ></div>
        </div>
        <div class="flex items-center justify-between flex-wrap gap-3">
          <p class="text-sm text-ink-soft">{{ progressText }}</p>
          <div v-if="!buildComplete && building" class="flex gap-2">
            <button
              v-if="!paused"
              class="px-4 py-1.5 rounded-md border border-line text-sm text-ink-soft transition-colors hover:bg-accent-soft hover:text-accent"
              @click="togglePause"
            >⏸ 暂停</button>
            <button
              v-else
              class="px-4 py-1.5 rounded-md bg-accent text-white text-sm transition-colors hover:bg-accent-hover"
              @click="togglePause"
            >▶ 继续</button>
          </div>
        </div>
        <div v-if="buildComplete" class="mt-4 flex gap-4 text-sm">
          <RouterLink to="/graph" class="text-accent hover:underline">→ 前往查看图谱</RouterLink>
          <RouterLink to="/chat" class="text-accent hover:underline">→ 前往智能问答</RouterLink>
        </div>
      </section>

      <!-- 失败章节 -->
      <section v-if="buildStarted && failedChapters.length" class="bg-card border border-line rounded-xl p-6 mb-5">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-base font-semibold text-ink">⚠️ 失败章节（{{ failedChapters.length }}）</h3>
          <button
            class="text-xs text-ink-faint hover:text-accent transition-colors"
            @click="fetchFailed"
          >刷新</button>
        </div>
        <ul class="divide-y divide-line">
          <li
            v-for="ch in failedChapters"
            :key="ch.index"
            class="py-3 flex items-center justify-between gap-3"
          >
            <div class="min-w-0">
              <div class="text-sm text-ink truncate">第 {{ ch.index + 1 }} 章 · {{ ch.title || '（无标题）' }}</div>
              <div v-if="ch.error" class="text-xs text-ink-faint truncate">{{ ch.error }}</div>
            </div>
            <button
              class="shrink-0 px-3 py-1.5 rounded-md border border-accent text-accent text-xs transition-colors hover:bg-accent-soft disabled:opacity-50"
              :disabled="retryingIndex === ch.index"
              @click="retryChapter(ch)"
            >{{ retryingIndex === ch.index ? '重试中...' : '重试' }}</button>
          </li>
        </ul>
        <p v-if="retryMessage" class="text-xs mt-3" :class="retryError ? 'text-red-600' : 'text-accent'">
          {{ retryMessage }}
        </p>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { apiFetch, apiGet, apiPost } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

// ── 文件选择与上传 ──
const fileInput = ref(null)
const selectedFile = ref(null)
const uploading = ref(false)
const uploadMessage = ref('')
const uploadError = ref(false)
const parseResult = ref(null)

const fileSizeMb = computed(() => (parseResult.value?.file_info?.size_mb || 0).toFixed(2))

function onFileSelect(e) {
  const file = e.target.files?.[0]
  if (file) {
    selectedFile.value = file
    uploadMessage.value = ''
    uploadError.value = false
  }
}

async function uploadFile() {
  if (!selectedFile.value || uploading.value) return
  uploading.value = true
  uploadMessage.value = ''
  uploadError.value = false
  try {
    // FormData 上传：不要手动设 Content-Type（浏览器自动带 boundary）
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    const resp = await apiFetch('/api/upload', { method: 'POST', body: formData })
    const data = await resp.json()
    if (!resp.ok) throw new Error(data.detail || '上传失败')
    parseResult.value = data
    uploadMessage.value = '✅ ' + data.message
    resetBuildState()
  } catch (e) {
    uploadError.value = true
    uploadMessage.value = '❌ ' + (e.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

// ── 编译控制 ──
const currentFilename = computed(() => parseResult.value?.filename || '')
const buildNovel = ref('')       // 后端返回的小说 key
const buildStarted = ref(false)
const building = ref(false)
const buildComplete = ref(false)
const paused = ref(false)
const progress = ref(0)
const progressTotal = ref(0)
const progressCompleted = ref(0)
const phase = ref('')
const buildError = ref('')
const rangeStart = ref(null)
const rangeEnd = ref(null)
let progressTimer = null

const progressPct = computed(() => (buildComplete.value ? 100 : Math.max(5, progress.value)))

const progressText = computed(() => {
  if (buildError.value) return '❌ ' + buildError.value
  if (buildComplete.value) return `编译完成，共 ${progressTotal.value} 章`
  if (paused.value) return `已暂停 · ${progressCompleted.value}/${progressTotal.value} 章`
  if (building.value) {
    const phaseText = phase.value === 'wiki' ? '章节解析' : (phase.value || '')
    return `编译中${phaseText ? '（' + phaseText + '）' : ''}... ${progressCompleted.value}/${progressTotal.value} 章 (${progress.value}%)`
  }
  return '等待中...'
})

function resetBuildState() {
  stopPolling()
  buildNovel.value = ''
  buildStarted.value = false
  building.value = false
  buildComplete.value = false
  paused.value = false
  progress.value = 0
  progressTotal.value = 0
  progressCompleted.value = 0
  phase.value = ''
  buildError.value = ''
  failedChapters.value = []
  retryMessage.value = ''
}

function startRangeBuild() {
  const start = parseInt(rangeStart.value)
  const end = parseInt(rangeEnd.value)
  if (!start || !end || start < 1 || end < start) {
    alert('请输入有效的章节范围（起始章 ≤ 结束章）')
    return
  }
  startBuild({ start_chapter: start, end_chapter: end })
}

async function startBuild(params) {
  if (!currentFilename.value) return
  buildStarted.value = true
  building.value = true
  buildComplete.value = false
  buildError.value = ''
  progress.value = 5
  try {
    const data = await apiPost('/api/build', { ...params, filename: currentFilename.value })
    buildNovel.value = data.novel
    progressTotal.value = data.total_chapters || 0
    startPolling()
  } catch (e) {
    building.value = false
    buildError.value = '启动失败: ' + (e.message || '')
  }
}

async function togglePause() {
  if (!buildNovel.value) return
  const target = !paused.value
  try {
    await apiPost(target ? '/api/build/pause' : '/api/build/resume', { novel: buildNovel.value })
    paused.value = target
  } catch (e) {
    // 状态不变，仅提示
    buildError.value = ''
  }
}

function startPolling() {
  stopPolling()
  progressTimer = setInterval(pollProgress, 2000)
}

function stopPolling() {
  if (progressTimer) {
    clearInterval(progressTimer)
    progressTimer = null
  }
}

async function pollProgress() {
  if (!buildNovel.value) return
  try {
    const pd = await apiGet('/api/build/progress?novel=' + encodeURIComponent(buildNovel.value))
    progress.value = pd.progress
    progressTotal.value = pd.total
    progressCompleted.value = pd.completed
    phase.value = pd.phase || ''
    if (pd.build_complete) {
      buildComplete.value = true
      building.value = false
      paused.value = false
      stopPolling()
      fetchFailed()
    }
  } catch (e) {
    // 轮询失败静默忽略，等下一轮（与旧版行为一致）
  }
}

// ── 失败章节 ──
const failedChapters = ref([])
const retryingIndex = ref(-1)
const retryMessage = ref('')
const retryError = ref(false)

async function fetchFailed() {
  if (!buildNovel.value) return
  try {
    const data = await apiGet('/api/build/failed?novel=' + encodeURIComponent(buildNovel.value))
    failedChapters.value = data.failed || []
  } catch (e) {
    // 忽略
  }
}

async function retryChapter(ch) {
  if (retryingIndex.value !== -1) return
  retryingIndex.value = ch.index
  retryMessage.value = ''
  retryError.value = false
  try {
    const data = await apiPost('/api/build/retry', {
      novel: buildNovel.value,
      chapter_index: ch.index,
    })
    if (data.status === 'ok') {
      retryMessage.value = `✅ 第 ${ch.index + 1} 章重试成功`
      failedChapters.value = failedChapters.value.filter((f) => f.index !== ch.index)
    } else {
      throw new Error(data.detail || '重试失败')
    }
  } catch (e) {
    retryError.value = true
    retryMessage.value = '❌ 第 ' + (ch.index + 1) + ' 章重试失败: ' + (e.message || '')
  } finally {
    retryingIndex.value = -1
  }
}

onBeforeUnmount(stopPolling)
</script>
