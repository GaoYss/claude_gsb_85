import { defineStore } from 'pinia'
import { ref } from 'vue'

import { fetchSummary } from '@/api/overview'

/**
 * 总览统计缓存：隐患批量操作（批量指派 / 批量催办）成功后调用 invalidate()，
 * 首页待办数量（未销号 / 逾期隐患）在下一次渲染前重新拉取，保证与列表、详情一致。
 */
export const useOverviewStore = defineStore('overview', () => {
  const summary = ref(null)
  const loading = ref(false)
  const dirty = ref(true)

  async function reload() {
    loading.value = true
    try {
      summary.value = await fetchSummary()
      dirty.value = false
    } finally {
      loading.value = false
    }
  }

  /** 进入总览页时调用：数据被标记过期（或尚未加载）才重新请求 */
  async function ensureLoaded() {
    if (dirty.value || !summary.value) await reload()
  }

  /** 数据变更方调用：标记总览统计已过期，并立即在后台刷新 */
  function invalidate() {
    dirty.value = true
    if (summary.value) reload().catch(() => {})
  }

  return { summary, loading, ensureLoaded, reload, invalidate }
})
