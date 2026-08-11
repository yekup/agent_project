import { defineStore } from 'pinia'

/**
 * 当前书籍（全局共享，跨页面保持选择）
 * value 为空字符串时表示默认书（后端行为：空 → 默认书）
 */
export const useNovelStore = defineStore('novel', {
  state: () => ({
    current: localStorage.getItem('currentNovel') || '',
    novels: [], // [{name, chapters, characters, ...}] 来自 /api/novels
  }),
  actions: {
    setCurrent(name) {
      this.current = name
      localStorage.setItem('currentNovel', name)
    },
    setNovels(list) {
      this.novels = list || []
    },
  },
})
