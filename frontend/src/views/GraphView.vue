<template>
  <div class="p-6 flex flex-col min-h-full">
    <!-- 标题与工具栏 -->
    <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
      <h2 class="text-xl font-semibold text-ink">🕸 人物关系图谱</h2>
      <div class="flex items-center gap-2 flex-wrap">
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索人物..."
          class="px-3 py-1.5 rounded-md bg-card border border-line text-ink text-sm w-28 outline-none focus:border-accent transition-colors"
          @keyup.enter="filterNode"
        />
        <button class="btn-ghost" @click="filterNode">🔍 聚焦</button>
        <button class="btn-ghost" @click="resetView">⤢ 全图</button>
        <button class="btn-solid" @click="openExportDialog">⬇️ 导出</button>
        <button
          v-if="editable"
          class="btn-ghost"
          :class="editMode ? '!bg-accent-soft !text-accent !border-accent' : ''"
          @click="toggleEditMode"
        >{{ editMode ? '✓ 完成编辑' : '✏️ 编辑' }}</button>
        <select
          v-model="roleFilter"
          class="px-2 py-1.5 rounded-md bg-card border border-line text-ink text-sm outline-none focus:border-accent transition-colors"
          @change="filterByRole"
        >
          <option value="all">全部角色</option>
          <option value="主角">主角</option>
          <option value="配角">配角</option>
        </select>
        <select
          v-if="communityList.length > 1"
          v-model="communityFilter"
          class="px-2 py-1.5 rounded-md bg-card border border-line text-ink text-sm outline-none focus:border-accent transition-colors"
          @change="applyCommunityHighlight"
        >
          <option value="all">全部社区</option>
          <option v-for="c in communityList" :key="c.id" :value="String(c.id)">
            社区 {{ c.id + 1 }}（{{ c.count }}人）
          </option>
        </select>
        <select
          v-model="isolatedToggle"
          class="px-2 py-1.5 rounded-md bg-card border border-line text-ink text-sm outline-none focus:border-accent transition-colors"
          @change="resetView"
        >
          <option value="connected">仅关联实体</option>
          <option value="all">显示全部节点</option>
        </select>
      </div>
    </div>

    <!-- 编辑模式提示 -->
    <p v-if="editMode" class="mb-2 text-xs text-accent bg-accent-soft border border-line rounded-md px-3 py-1.5">
      编辑模式：右键点击节点可合并，右键点击关系线可修改或删除。
    </p>

    <!-- 图谱区域 -->
    <div class="relative">
      <div
        v-show="!showEmpty"
        ref="containerRef"
        class="w-full h-[calc(100vh-15rem)] min-h-[480px] rounded-lg overflow-hidden bg-card border border-line"
      ></div>

      <!-- 空态 -->
      <div
        v-if="showEmpty"
        class="flex flex-col items-center justify-center text-center h-[calc(100vh-15rem)] min-h-[480px] bg-card border border-line rounded-lg"
      >
        <span class="text-5xl mb-4 opacity-30">🕸</span>
        <h3 class="text-lg font-semibold text-ink-soft mb-2">暂无图谱数据</h3>
        <p class="text-ink-faint text-sm max-w-md">{{ emptyMessage }}</p>
        <RouterLink
          v-if="auth.has('page:upload')"
          to="/upload"
          class="btn-solid mt-6"
        >⬆️ 前往上传</RouterLink>
      </div>

      <!-- 加载中 -->
      <div
        v-if="loading"
        class="absolute inset-0 flex items-center justify-center bg-card/60 rounded-lg"
      >
        <span class="text-sm text-ink-faint">加载中...</span>
      </div>

      <!-- 节点详情面板 -->
      <div
        v-if="detail.visible"
        class="absolute top-2 right-2 z-20 w-[380px] max-w-[90%] max-h-[calc(100%-16px)] flex flex-col bg-card border border-line rounded-lg shadow-lg"
      >
        <div class="p-4 pb-2 border-b border-line shrink-0">
          <div class="flex items-center justify-between">
            <h3 class="text-lg font-semibold text-ink">{{ detail.name }}</h3>
            <button class="text-ink-faint hover:text-ink text-lg leading-none transition-colors" @click="closeDetail">×</button>
          </div>
          <p class="text-ink-soft text-xs mt-0.5">角色: {{ detail.role }}</p>
          <p class="text-ink-soft text-xs mt-1">关联: {{ relAll.length }} 人 | 出场: {{ detail.mention }} 次</p>
        </div>
        <div class="shrink-0 px-4 pt-3 pb-1">
          <h4 class="text-sm text-ink-soft">关联关系 <span class="text-ink-faint font-normal">({{ relAll.length }}条)</span></h4>
        </div>
        <div class="px-4 pb-3 overflow-y-auto flex-1 max-h-[360px]">
          <div
            v-for="(r, i) in relPageItems"
            :key="i"
            class="py-1.5 border-b border-line/60 last:border-0 text-sm text-ink break-words"
          >
            <span class="inline-flex items-center gap-1.5">
              <span class="inline-block w-2 h-2 rounded-full shrink-0" :style="{ background: eColor[classifyRelation(r.relation || '')] }"></span>
              <span class="font-medium">{{ r.source === detail.name ? r.target : r.source }}</span>
            </span>
            <div class="text-ink-faint mt-0.5 pl-4 text-xs" :title="r.relation || ''">
              {{ truncateRel(r.relation || '') }}
            </div>
          </div>
        </div>
        <div v-if="relTotalPages > 1" class="shrink-0 px-4 pb-3 pt-1">
          <div class="flex items-center justify-between text-xs text-ink-soft">
            <button class="btn-ghost px-2 py-1 text-xs disabled:opacity-30" :disabled="relPage <= 0" @click="relPage--">上一页</button>
            <span>{{ relPage + 1 }}/{{ relTotalPages }}</span>
            <button class="btn-ghost px-2 py-1 text-xs disabled:opacity-30" :disabled="relPage >= relTotalPages - 1" @click="relPage++">下一页</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部信息与图例 -->
    <div class="flex justify-between items-center mt-2 flex-wrap gap-2">
      <p class="text-sm text-ink-faint">{{ graphInfo }}</p>
      <div class="flex gap-4 text-xs text-ink-faint">
        <span><span class="inline-block w-3 h-3 rounded-full mr-1" style="background:#d95f4e"></span>敌对/冲突</span>
        <span><span class="inline-block w-3 h-3 rounded-full mr-1" style="background:#5b8dd9"></span>同盟/友好</span>
        <span><span class="inline-block w-3 h-3 rounded-full mr-1" style="background:#9b9a97"></span>从属/上下级</span>
        <span><span class="inline-block w-3 h-3 rounded-full mr-1" style="background:#2f6f4f"></span>其他关系</span>
      </div>
    </div>

    <!-- 悬浮提示 -->
    <div
      v-show="tooltip.visible"
      class="fixed z-50 bg-card border border-line rounded-md px-2.5 py-1.5 text-xs text-ink shadow pointer-events-none"
      :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }"
    >{{ tooltip.text }}</div>

    <!-- 导出对话框 -->
    <div
      v-if="exportOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/30"
      @click.self="exportOpen = false"
    >
      <div class="bg-card border border-line rounded-xl p-6 min-w-[400px] max-w-[480px] shadow-xl">
        <h3 class="text-lg font-semibold text-ink mb-4">⬇️ 导出图谱</h3>
        <div class="modal-row">
          <label>导出格式</label>
          <div class="flex gap-3 text-sm text-ink">
            <label class="cursor-pointer"><input v-model="expFmt" type="radio" value="png" class="mr-1" />PNG</label>
            <label class="cursor-pointer"><input v-model="expFmt" type="radio" value="svg" class="mr-1" />SVG</label>
          </div>
        </div>
        <div class="modal-row">
          <label>背景色</label>
          <div class="flex gap-3 text-sm text-ink">
            <label class="cursor-pointer"><input v-model="expBg" type="radio" value="dark" class="mr-1" />深色</label>
            <label class="cursor-pointer"><input v-model="expBg" type="radio" value="white" class="mr-1" />白色</label>
          </div>
        </div>
        <div v-show="expFmt === 'png'" class="modal-row">
          <label>分辨率 (PNG)</label>
          <div class="flex gap-3 text-sm text-ink">
            <label class="cursor-pointer"><input v-model="expRes" type="radio" value="1" class="mr-1" />1x</label>
            <label class="cursor-pointer"><input v-model="expRes" type="radio" value="2" class="mr-1" />2x</label>
            <label class="cursor-pointer"><input v-model="expRes" type="radio" value="4" class="mr-1" />4x</label>
          </div>
        </div>
        <div class="modal-row">
          <label>包含图例</label>
          <input v-model="expLegend" type="checkbox" />
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button class="btn-ghost text-sm" @click="exportOpen = false">取消</button>
          <button class="btn-solid text-sm" @click="doExport">导出</button>
        </div>
        <p class="text-xs text-ink-faint mt-2">{{ expFmt === 'png' ? 'PNG 原生输出，无需截图' : 'SVG 内嵌高清图，可无限缩放' }}</p>
      </div>
    </div>

    <!-- 节点编辑菜单 -->
    <div
      v-if="nodeMenu.open"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/30"
      @click.self="nodeMenu.open = false"
    >
      <div class="bg-card border border-line rounded-xl p-5 w-96 max-w-[92vw] shadow-xl">
        <h3 class="font-semibold text-ink mb-3">✏️ 编辑节点：{{ nodeMenu.nodeId }}</h3>
        <div class="space-y-2">
          <button
            class="w-full text-left px-3 py-2 rounded-md bg-paper border border-line text-sm text-ink hover:bg-accent-soft hover:text-accent transition-colors"
            @click="openMergeDialog"
          >⛙ 合并到其他节点</button>
          <button
            class="w-full text-left px-3 py-2 rounded-md bg-paper border border-line text-sm text-ink-faint hover:bg-accent-soft transition-colors"
            @click="nodeMenu.open = false"
          >× 取消</button>
        </div>
      </div>
    </div>

    <!-- 边编辑菜单 -->
    <div
      v-if="edgeMenu.open"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/30"
      @click.self="edgeMenu.open = false"
    >
      <div class="bg-card border border-line rounded-xl p-5 w-96 max-w-[92vw] shadow-xl">
        <h3 class="font-semibold text-ink mb-3">✏️ 编辑关系</h3>
        <p class="text-sm text-ink-soft mb-3">{{ edgeMenu.source }} → {{ edgeMenu.target }}</p>
        <input
          v-model="edgeMenu.relation"
          type="text"
          placeholder="输入新关系描述"
          class="w-full px-3 py-2 rounded-md bg-paper border border-line text-ink text-sm outline-none focus:border-accent transition-colors"
        />
        <div class="flex gap-2 mt-4">
          <button class="btn-solid text-sm flex-1" @click="updateRelation">💾 修改</button>
          <button class="btn-ghost text-sm !text-red-600 !border-red-300 hover:!bg-red-50" @click="deleteEdge">🗑 删除</button>
          <button class="btn-ghost text-sm" @click="edgeMenu.open = false">取消</button>
        </div>
      </div>
    </div>

    <!-- 合并节点对话框 -->
    <div
      v-if="mergeDialog.open"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/30"
      @click.self="mergeDialog.open = false"
    >
      <div class="bg-card border border-line rounded-xl p-5 w-96 max-w-[92vw] shadow-xl">
        <h3 class="font-semibold text-ink mb-3">⛙ 合并节点</h3>
        <p class="text-sm text-ink-soft mb-3">将 <b class="text-ink">{{ mergeDialog.source }}</b> 合并到：</p>
        <input
          v-model="mergeDialog.target"
          type="text"
          placeholder="输入目标人物名"
          class="w-full px-3 py-2 rounded-md bg-paper border border-line text-ink text-sm outline-none focus:border-accent transition-colors"
        />
        <div class="flex gap-2 mt-4">
          <button class="btn-solid text-sm flex-1" @click="doMerge">⛙ 合并</button>
          <button class="btn-ghost text-sm" @click="mergeDialog.open = false">取消</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import { apiGet, apiPost } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useNovelStore } from '../stores/novel'

const auth = useAuthStore()
const novel = useNovelStore()

/* ── 关系分类与配色（移植自旧版 graph.html；色相调整为浅色阅读风） ── */
function classifyRelation(t) {
  const s = (t || '').toLowerCase()
  return /敌|仇|杀|战|斗|打|败|害|反|叛|怒|恨|争|怨/.test(s) ? 'hostile'
    : /友|盟|同|帮|助|救|忠|护|师|徒|爱|情|亲/.test(s) ? 'friendly'
    : /属|下|上|级|从|臣|君|主/.test(s) ? 'subordinate' : 'other'
}
const eColor = { hostile: '#d95f4e', friendly: '#5b8dd9', subordinate: '#9b9a97', other: '#2f6f4f' }
const nFill = { '主角': '#d9a441', '配角': '#5b8dd9' }
function calcDeg(e) {
  const d = {}
  e.forEach((r) => { d[r.source] = (d[r.source] || 0) + 1; d[r.target] = (d[r.target] || 0) + 1 })
  return d
}
const truncateRel = (t) => (t.length > 30 ? t.slice(0, 28) + '…' : t)

/* ── 状态 ── */
const containerRef = ref(null)
let cy = null // cytoscape 实例不放进响应式系统
let allNodes = []
let allEdges = []
let _cfg = {}
let _initialZoomDone = false
let _currentPad = 50
let _nodeToCommunity = {}

const loading = ref(false)
const showEmpty = ref(false)
const emptyMessage = ref('请先上传并编译小说，图谱数据将在编译完成后自动生成。')
const graphInfo = ref('')
const searchQuery = ref('')
const roleFilter = ref('all')
const communityFilter = ref('all')
const isolatedToggle = ref('connected')
const effectiveNovel = ref('') // 实际请求所用的书籍名（空选择时回退到第一本）

const REL_PAGE = 15
const detail = reactive({ visible: false, name: '', role: '', mention: 0 })
const relAll = ref([])
const relPage = ref(0)
const relTotalPages = computed(() => Math.ceil(relAll.value.length / REL_PAGE))
const relPageItems = computed(() => relAll.value.slice(relPage.value * REL_PAGE, relPage.value * REL_PAGE + REL_PAGE))

const tooltip = reactive({ visible: false, text: '', x: 0, y: 0 })

const editable = ref(false)
const editMode = ref(false)
const nodeMenu = reactive({ open: false, nodeId: '' })
const edgeMenu = reactive({ open: false, source: '', target: '', relation: '' })
const mergeDialog = reactive({ open: false, source: '', target: '' })

const exportOpen = ref(false)
const expFmt = ref('png')
const expBg = ref('dark')
const expRes = ref('2')
const expLegend = ref(true)

const communityList = computed(() => {
  const counts = {}
  Object.values(_nodeToCommunity).forEach((c) => { counts[c] = (counts[c] || 0) + 1 })
  return Object.keys(counts).map(Number).sort((a, b) => a - b).map((id) => ({ id, count: counts[id] }))
})

/* ── 数据加载 ── */
async function loadGraph() {
  closeDetail()
  destroyCy()
  loading.value = true
  graphInfo.value = '加载中...'
  try {
    let name = novel.current || ''
    let d = await apiGet('/api/graph?novel=' + encodeURIComponent(name))
    // 未选书时后端返回空 + novels 列表：回退到第一本（沿用旧版自动选书行为）
    if ((!d.nodes || d.nodes.length === 0) && !name && Array.isArray(d.novels) && d.novels.length > 0) {
      name = d.novels[0].name
      d = await apiGet('/api/graph?novel=' + encodeURIComponent(name))
    }
    effectiveNovel.value = name
    if (d.error || !d.nodes || d.nodes.length === 0) {
      showEmpty.value = true
      emptyMessage.value = d.error || (name ? '该书尚未编译，图谱数据将在编译完成后自动生成。' : '请先上传并编译小说，图谱数据将在编译完成后自动生成。')
      graphInfo.value = d.error || '暂无数据'
      return
    }
    showEmpty.value = false
    allNodes = d.nodes || []
    allEdges = d.edges || []
    _cfg = d.layout || {}
    _nodeToCommunity = (d.communities && d.communities.node_to_community) || {}
    graphInfo.value = `${allNodes.length} 角色, ${allEdges.length} 关系`
    _cfg.minDegree = _cfg.minDegree || Math.max(2, Math.min(5, Math.floor(allNodes.length * 0.005)))
    _cfg.edgeLimit = _cfg.edgeLimit || Math.min(300 + allNodes.length * 0.3, 600)
    renderGraph(allNodes, allEdges, 'full')
  } catch (e) {
    showEmpty.value = true
    emptyMessage.value = '图谱数据加载失败，请稍后重试。'
    graphInfo.value = '加载失败'
    console.warn('[Graph] 加载失败:', e)
  } finally {
    loading.value = false
  }
}

/* ── 渲染（核心逻辑严格移植旧版 renderGraph） ── */
function renderGraph(nodes, edges, mode, centerNode) {
  _initialZoomDone = false
  destroyCy()
  const container = containerRef.value
  if (!container) return
  const degrees = calcDeg(edges)
  const showAll = isolatedToggle.value === 'all'

  let sn, se
  if (mode === 'focus' && centerNode) {
    const d = new Set([centerNode])
    edges.forEach((e) => { if (e.source === centerNode) d.add(e.target); if (e.target === centerNode) d.add(e.source) })
    const id = new Set(d)
    edges.forEach((e) => { if (d.has(e.source) && !d.has(e.target)) id.add(e.target); if (d.has(e.target) && !d.has(e.source)) id.add(e.source) })
    sn = nodes.filter((n) => id.has(n.name))
    se = edges.filter((e) => new Set(sn.map((n) => n.name)).has(e.source) && new Set(sn.map((n) => n.name)).has(e.target))
  } else {
    const minDeg = showAll ? 0 : _cfg.minDegree || 3
    sn = nodes.filter((n) => showAll || (degrees[n.name] || 0) >= minDeg)
    const sns = new Set(sn.map((n) => n.name))
    se = edges.filter((e) => sns.has(e.source) && sns.has(e.target))
  }
  se.sort((a, b) => (b.weight || 0) - (a.weight || 0))
  const el = mode === 'focus' ? Math.min(250, _cfg.edgeLimit * 0.5) : (_cfg.edgeLimit || 500)
  se = se.slice(0, Math.min(el, se.length))
  const fs = new Set()
  se.forEach((e) => { fs.add(e.source); fs.add(e.target) })
  sn = sn.filter((n) => fs.has(n.name) || (mode === 'focus' && n.name === centerNode))
  const fn = new Set(sn.map((n) => n.name))
  se = se.filter((e) => fn.has(e.source) && fn.has(e.target))
  const fd = calcDeg(se)
  const mxd = Math.max(1, ...Object.values(fd))
  const els = []
  se.forEach((e) => {
    const r = classifyRelation(e.relation || '')
    const k = (e.weight || 1) >= 3 || r === 'hostile'
    const ind = mode === 'focus' && centerNode && e.source !== centerNode && e.target !== centerNode
    els.push({ data: { id: 'e_' + e.source + '_' + e.target, source: e.source, target: e.target, relType: r, isKey: k, weight: e.weight || 1, isIndirect: ind || false } })
  })
  sn.forEach((n) => {
    const dg = fd[n.name] || 0
    const ratio = Math.log2(Math.max(dg, 1) + 1) / Math.log2(mxd + 1)
    const ms = _cfg.maxNodeSize || 12
    const mn = _cfg.minNodeSize || 3
    const sz = mode === 'focus'
      ? (n.name === centerNode ? Math.min(ms * 1.2, 40) : Math.max(mn, mn + ratio * (ms * 0.8)))
      : Math.max(mn, mn + ratio * ms)
    let fl = 0
    if (mode === 'focus' && centerNode) {
      if (n.name === centerNode) fl = 0
      else if (se.some((e) => (e.source === centerNode && e.target === n.name) || (e.target === centerNode && e.source === n.name))) fl = 1
      else fl = 2
    }
    els.push({ data: { id: n.name, label: n.name, role: n.role || '未知', mention: n.mention_count || 0, deg: dg || 0, size: Math.round(sz), fl } })
  })

  const style = [
    { selector: 'node', style: {
      label: 'data(label)',
      'font-size': (ele) => Math.max(4, Math.min(11, ele.data('size') * 0.22)) + 'px',
      color: '#37352f',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 1,
      width: 'data(size)',
      height: 'data(size)',
      'border-width': (ele) => (ele.data('fl') === 0 ? 3 : (ele.data('deg') > 10 ? 2 : 1)),
      'border-color': (ele) => (ele.data('fl') === 0 ? '#d9a441' : '#ffffff'),
      'background-color': (ele) => nFill[ele.data('role')] || '#9b9a97',
      'min-zoomed-font-size': 1,
      'z-index': (ele) => -ele.data('size'),
    } },
    { selector: 'edge', style: {
      width: (ele) => Math.max(0.1, Math.min(1, (ele.data('weight') || 1) * 0.12)),
      'line-color': (ele) => eColor[ele.data('relType')] || '#9b9a97',
      opacity: (ele) => {
        if (mode === 'focus' && ele.data('isIndirect')) return 0.01
        return ele.data('isKey') ? 0.3 : 0.03
      },
      'curve-style': 'haystack',
      'haystack-radius': 0.3,
      'line-style': (ele) => (ele.data('relType') === 'hostile' ? 'dashed' : 'solid'),
    } },
    { selector: ':selected', style: { 'border-color': '#d9a441', 'border-width': 3 } },
  ]

  const instance = cytoscape({ container, elements: els, style, minZoom: 0.02, maxZoom: 8 })
  cy = instance
  cy.on('tap', 'node', (evt) => showNodeDetail(evt.target.id()))
  cy.on('tap', (evt) => { if (evt.target === cy) closeDetail() })
  cy.on('cxttap', 'node', (evt) => {
    if (!editMode.value) return
    evt.originalEvent.preventDefault()
    showNodeEditMenu(evt.target.id())
  })
  cy.on('cxttap', 'edge', (evt) => {
    if (!editMode.value) return
    evt.originalEvent.preventDefault()
    const e = evt.target
    showEdgeEditMenu(e.data('source'), e.data('target'))
  })
  cy.on('mouseover', 'node', (evt) => {
    const n = evt.target
    tooltip.text = `${n.data('label')} | ${n.data('role')} | 关联${n.data('deg')}人 | 出场${n.data('mention')}次`
    tooltip.x = evt.originalEvent.clientX + 12
    tooltip.y = evt.originalEvent.clientY - 10
    tooltip.visible = true
  })
  cy.on('mouseout', 'node', () => { tooltip.visible = false })

  const rep = _cfg.nodeRepulsion || 800000
  const grv = _cfg.gravity || 0.01
  const ide = _cfg.idealEdgeLength || Math.min(300, 80 + Math.sqrt(Math.max(allNodes.length, 10)) * 8)
  const niter = _cfg.numIter || Math.min(2000 + allNodes.length * 3, 4000)
  const pad = mode === 'focus' ? 70 : 50
  _currentPad = pad

  instance.on('layoutstop', () => {
    // 40 轮节点分离迭代（旧版原样移植，勿改数值）
    const ST = Math.max(15, _cfg.sepThreshold || 20)
    for (let i = 0; i < 40; i++) {
      let mm = 0
      const na = instance.nodes()
      for (let x = 0; x < na.length; x++) {
        const a = na[x]
        const pa = a.position()
        const sa = a.data('size') || 6
        for (let y = x + 1; y < na.length; y++) {
          const b = na[y]
          const pb = b.position()
          const sb = b.data('size') || 6
          const dx = pb.x - pa.x
          const dy = pb.y - pa.y
          const d = Math.sqrt(dx * dx + dy * dy)
          const min = (sa + sb) / 2 + ST
          if (d < min && d > 0.01) {
            const p = ((min - d) / 2) * 0.6
            b.position({ x: pb.x + (dx / d) * p, y: pb.y + (dy / d) * p })
            a.position({ x: pa.x - (dx / d) * p, y: pa.y - (dy / d) * p })
            mm = Math.max(mm, p)
          }
        }
      }
      if (mm < 0.3) break
    }
    instance.fit(undefined, pad)
    instance.center()
    if (mode === 'focus' && centerNode) {
      const ele = instance.getElementById(centerNode)
      if (ele.length > 0) setTimeout(() => instance.fit(ele, pad + 20), 100)
    } else if (!_initialZoomDone) {
      _initialZoomDone = true
      const zj = sn.find((n) => n.name.includes('赵玖'))
      if (zj) setTimeout(() => instance.fit(instance.getElementById(zj.name), pad + 30), 200)
    }
    const fc = nodes.length - sn.length
    graphInfo.value = `${sn.length} 角色, ${se.length} 关系${fc > 0 ? ` (过滤 ${fc} 个节点)` : ''} · ${mode === 'focus' ? '聚焦: ' + (centerNode || '') : '全图'}`
  })

  instance.layout({
    name: 'cose',
    animate: false,
    nodeRepulsion: showAll ? Math.min(rep * 1.3, 3000000) : Math.min(rep, 2000000),
    idealEdgeLength: showAll ? Math.min(ide * 1.2, 400) : ide,
    edgeElasticity: 0.3,
    gravity: showAll ? Math.max(0.003, grv * 0.5) : Math.max(0.003, grv * 0.8),
    numIter: niter,
    initialTemp: 3000,
    coolingFactor: 0.98,
    minTemp: 0.5,
  }).run()

  // 按缩放级别控制标签显隐（旧版原样移植）
  let _lt = null
  const fh = _cfg.labelHideFull || 0.07
  const ch = _cfg.labelHideCore || 0.15
  cy.on('zoom', () => {
    if (_lt) clearTimeout(_lt)
    const z = cy.zoom()
    cy.nodes().forEach((n) => {
      const fl = n.data('fl') || 0
      const dg = n.data('deg') || 0
      let op = 1
      if (z < fh) op = 0
      else if (z < ch) op = (fl === 0 || dg >= 8) ? 1 : 0
      else if (z < ch * 1.5) op = (dg >= 3 || fl === 0) ? 1 : 0.3
      n.style({ 'text-opacity': op })
    })
    _lt = setTimeout(() => {
      if (cy.zoom() > ch * 1.7) cy.nodes().forEach((n) => n.style({ 'text-opacity': 1 }))
    }, 300)
  })
}

function destroyCy() {
  if (cy) {
    cy.removeAllListeners()
    cy.destroy()
    cy = null
  }
  tooltip.visible = false
}

/* ── 详情面板 ── */
function showNodeDetail(id) {
  const n = allNodes.find((x) => x.name === id)
  if (!n) return
  detail.name = n.name
  detail.role = n.role || '未知'
  detail.mention = n.mention_count || 0
  relAll.value = allEdges.filter((e) => e.source === id || e.target === id)
  relPage.value = 0
  detail.visible = true
}
function closeDetail() {
  detail.visible = false
  detail.name = ''
  relAll.value = []
  relPage.value = 0
}

/* ── 交互动作 ── */
function filterNode() {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) { resetView(); return }
  const m = allNodes.filter((n) => n.name.toLowerCase().includes(q))
  if (m.length === 0) { window.alert('未找到匹配的人物'); return }
  closeDetail()
  renderGraph(allNodes, allEdges, 'focus', m[0].name)
}
function resetView() {
  closeDetail()
  roleFilter.value = 'all'
  communityFilter.value = 'all'
  _initialZoomDone = false
  renderGraph(allNodes, allEdges, 'full')
}
function filterByRole() {
  closeDetail()
  const r = roleFilter.value
  if (r === 'all') { renderGraph(allNodes, allEdges, 'full'); return }
  const f = allNodes.filter((n) => n.role === r)
  const ns = new Set(f.map((n) => n.name))
  const e = allEdges.filter((x) => ns.has(x.source) && ns.has(x.target))
  renderGraph(f, e, 'full')
}

// 社区高亮：数据含 communities.node_to_community 时可用（旧版无此功能，按高亮实现）
function applyCommunityHighlight() {
  if (!cy) return
  const v = communityFilter.value
  if (v === 'all') {
    cy.elements().removeStyle()
    return
  }
  const cid = Number(v)
  const inC = (name) => _nodeToCommunity[name] === cid
  cy.batch(() => {
    cy.nodes().forEach((n) => {
      n.style(inC(n.id())
        ? { opacity: 1, 'text-opacity': 1 }
        : { opacity: 0.12, 'text-opacity': 0 })
    })
    cy.edges().forEach((e) => {
      if (!inC(e.data('source')) || !inC(e.data('target'))) e.style({ opacity: 0.02 })
    })
  })
}

/* ── 编辑 ── */
function preventCtx(e) { e.preventDefault() }
function toggleEditMode() {
  editMode.value = !editMode.value
  const container = containerRef.value
  if (cy) {
    if (editMode.value) cy.nodes().style('border-color', '#d9a441')
    else cy.nodes().removeStyle('border-color')
  }
  if (container) {
    if (editMode.value) container.addEventListener('contextmenu', preventCtx, true)
    else container.removeEventListener('contextmenu', preventCtx, true)
  }
}
function showNodeEditMenu(nodeId) {
  nodeMenu.nodeId = nodeId
  nodeMenu.open = true
}
function showEdgeEditMenu(source, target) {
  edgeMenu.source = source
  edgeMenu.target = target
  edgeMenu.relation = ''
  edgeMenu.open = true
}
async function deleteEdge() {
  const { source, target } = edgeMenu
  if (!window.confirm(`确定删除 ${source} → ${target} 的关系？`)) return
  try {
    await apiPost('/api/graph/edit/delete-edge', { novel: effectiveNovel.value, source, target })
    edgeMenu.open = false
    window.alert('已删除')
    await loadGraph()
  } catch (e) {
    window.alert('失败: ' + e.message)
  }
}
async function updateRelation() {
  const { source, target, relation } = edgeMenu
  if (!relation) { window.alert('请输入关系描述'); return }
  try {
    await apiPost('/api/graph/edit/update-relation', { novel: effectiveNovel.value, source, target, relation })
    edgeMenu.open = false
    window.alert('已更新')
    await loadGraph()
  } catch (e) {
    window.alert('失败: ' + e.message)
  }
}
function openMergeDialog() {
  mergeDialog.source = nodeMenu.nodeId
  mergeDialog.target = ''
  nodeMenu.open = false
  mergeDialog.open = true
}
async function doMerge() {
  const { source, target } = mergeDialog
  if (!source || !target) { window.alert('请输入目标人物名'); return }
  if (!window.confirm(`确定将「${source}」合并到「${target}」？此操作不可撤销。`)) return
  try {
    await apiPost('/api/graph/edit/merge-nodes', { novel: effectiveNovel.value, source, target })
    mergeDialog.open = false
    window.alert('已合并')
    await loadGraph()
  } catch (e) {
    window.alert('失败: ' + e.message)
  }
}

/* ── 导出（移植旧版导出逻辑，适配浅色主题标签色） ── */
const LEGEND_ITEMS = [
  ['#d95f4e', '敌对/冲突'],
  ['#5b8dd9', '同盟/友好'],
  ['#9b9a97', '从属/上下级'],
  ['#2f6f4f', '其他关系'],
]
function getNovelName() {
  const hit = novel.novels.find((n) => n.name === effectiveNovel.value)
  return hit ? (hit.display_name || hit.name) : (effectiveNovel.value || 'graph')
}
function getLegendSVG(bg) {
  const isDark = bg !== '#ffffff'
  const tx = isDark ? '#f3f4f6' : '#1f2937'
  const box = isDark ? 'rgba(17,24,39,0.85)' : 'rgba(255,255,255,0.85)'
  const bd = isDark ? '#4b5563' : '#d1d5db'
  let l = `<rect x="0" y="0" width="210" height="110" rx="6" fill="${box}" stroke="${bd}" stroke-width="1"/><text x="12" y="20" fill="${tx}" font-size="13" font-family="sans-serif">关系类型</text>`
  LEGEND_ITEMS.forEach((o, i) => {
    const yy = 36 + i * 18
    l += `<circle cx="16" cy="${yy}" r="5" fill="${o[0]}"/><text x="28" y="${yy + 4}" fill="${tx}" font-size="11" font-family="sans-serif">${o[1]}</text>`
  })
  return '<svg xmlns="http://www.w3.org/2000/svg" width="210" height="110">' + l + '</svg>'
}
function downloadBlob(d, f) {
  const a = document.createElement('a')
  a.download = f
  a.href = d
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}
function openExportDialog() {
  if (!cy) { window.alert('图谱尚未加载'); return }
  exportOpen.value = true
}
function doExport() {
  if (!cy) { window.alert('图谱未加载'); return }
  exportOpen.value = false
  const bgC = expBg.value === 'white' ? '#ffffff' : '#111827'
  const nC = expBg.value === 'white' ? '#1f2937' : '#f3f4f6'
  const leg = expLegend.value
  cy.style().selector('node').style('color', nC).update()
  if (expFmt.value === 'png') {
    const res = parseInt(expRes.value || '2', 10)
    const d = cy.png({ scale: res, bg: bgC, full: true })
    if (leg) {
      const c = document.createElement('canvas')
      const img = new Image()
      img.onload = () => {
        const w = cy.width() * res
        const h = cy.height() * res
        c.width = w
        c.height = h + 130 * res
        const ctx = c.getContext('2d')
        ctx.drawImage(img, 0, 0)
        const li = new Image()
        li.onload = () => {
          ctx.drawImage(li, w - 230 * res, h + 10 * res, 210 * res, 110 * res)
          downloadBlob(c.toDataURL('image/png'), getNovelName() + '_graph.png')
        }
        li.src = 'data:image/svg+xml,' + encodeURIComponent(getLegendSVG(bgC))
      }
      img.src = d
      return
    }
    downloadBlob(d, getNovelName() + '_graph.png')
    return
  }
  // SVG：内嵌 PNG 位图 + 图例（旧版原样方案）
  const s = 2
  const d = cy.png({ scale: s, bg: bgC, full: true })
  const w = cy.width()
  const h = cy.height()
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h + (leg ? 130 : 0)}" viewBox="0 0 ${w} ${h + (leg ? 130 : 0)}"><image href="${d}" width="${w}" height="${h}"/>`
  if (leg) {
    svg += `<foreignObject x="${w - 220}" y="${h + 10}" width="210" height="110"><div xmlns="http://www.w3.org/1999/xhtml" style="background:${bgC};border:1px solid #d1d5db;border-radius:6px;padding:8px;font-family:sans-serif;font-size:12px;color:${nC}"><b style="font-size:13px">关系类型</b>`
    LEGEND_ITEMS.forEach((o) => {
      svg += `<div style="margin-top:4px"><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${o[0]};margin-right:6px"></span>${o[1]}</div>`
    })
    svg += '</div></foreignObject>'
  }
  svg += '</svg>'
  const b = new Blob([svg], { type: 'image/svg+xml' })
  const u = URL.createObjectURL(b)
  downloadBlob(u, getNovelName() + '_graph.svg')
  URL.revokeObjectURL(u)
  cy.style().selector('node').style('color', '#37352f').update()
}

/* ── 生命周期 ── */
function onResize() {
  clearTimeout(window._graphRt)
  window._graphRt = setTimeout(() => {
    if (cy && cy.nodes().length > 0) {
      cy.fit(undefined, _currentPad)
      cy.center()
    }
  }, 300)
}

watch(() => novel.current, () => {
  communityFilter.value = 'all'
  roleFilter.value = 'all'
  searchQuery.value = ''
  loadGraph()
})

onMounted(async () => {
  window.addEventListener('resize', onResize)
  // 编辑权限检查（editable 时才渲染编辑入口）
  apiGet('/api/graph/edit/check')
    .then((d) => { editable.value = !!d.editable })
    .catch(() => {})
  await loadGraph()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (containerRef.value) containerRef.value.removeEventListener('contextmenu', preventCtx, true)
  destroyCy()
})
</script>

<style scoped>
.btn-ghost {
  @apply px-3 py-1.5 rounded-md bg-card border border-line text-sm text-ink-soft hover:bg-accent-soft hover:text-accent transition-colors;
}
.btn-solid {
  @apply px-3 py-1.5 rounded-md bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors;
}
.modal-row {
  @apply flex justify-between items-center py-2 border-b border-line/60 last:border-0;
}
.modal-row > label {
  @apply text-ink-soft text-sm;
}
</style>
