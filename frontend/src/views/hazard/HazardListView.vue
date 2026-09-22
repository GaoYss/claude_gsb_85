<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import {
  batchAssignHazards,
  batchRemindHazards,
  deleteHazard,
  fetchHazards,
} from '@/api/hazards'
import DataTable from '@/components/common/DataTable.vue'
import PaginationBar from '@/components/common/PaginationBar.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import BatchActionDialog from '@/components/hazard/BatchActionDialog.vue'
import BatchResultDialog from '@/components/hazard/BatchResultDialog.vue'
import HazardFilterBar from '@/components/hazard/HazardFilterBar.vue'
import { useListQuery } from '@/composables/useListQuery'
import { useConfirmStore } from '@/stores/confirm'
import { useDictionaryStore } from '@/stores/dictionary'
import { useOverviewStore } from '@/stores/overview'
import { useToastStore } from '@/stores/toast'
import { deadlineHint, formatDate } from '@/utils/format'

const dictionary = useDictionaryStore()
const toast = useToastStore()
const confirm = useConfirmStore()
const overview = useOverviewStore()

const { filters, page, pageSize, syncQuery, reset } = useListQuery({
  keyword: '',
  reservoir_id: '',
  category: '',
  severity: '',
  status: '',
  overdue_only: false,
  open_only: false,
})

const rows = ref([])
const total = ref(0)
const pages = ref(0)
const loading = ref(false)

const columns = [
  { key: 'code', label: '隐患编号', width: '150px' },
  { key: 'title', label: '隐患标题' },
  { key: 'reservoir', label: '水库', width: '140px' },
  { key: 'category', label: '类别', width: '100px' },
  { key: 'severity', label: '等级', width: '110px' },
  { key: 'status', label: '整改状态', width: '110px' },
  { key: 'discovered_on', label: '发现日期', width: '120px' },
  { key: 'deadline', label: '整改期限', width: '170px' },
  { key: 'actions', label: '操作', width: '150px' },
]

// 跨页勾选：选择集不随翻页 / 筛选清空，提交时以后端实际处理数量对账
const selected = ref(new Map())
const selectedCount = computed(() => selected.value.size)

function toggleRow(row) {
  if (selected.value.has(row.id)) selected.value.delete(row.id)
  else selected.value.set(row.id, row)
}

function toggleAll(pageRows) {
  const allChecked = pageRows.every((row) => selected.value.has(row.id))
  for (const row of pageRows) {
    if (allChecked) selected.value.delete(row.id)
    else selected.value.set(row.id, row)
  }
}

function clearSelection() {
  selected.value.clear()
}

async function load() {
  loading.value = true
  try {
    const data = await fetchHazards({
      ...filters.value,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = data.items
    total.value = data.total
    pages.value = data.pages
  } catch (error) {
    toast.error(error.message)
  } finally {
    loading.value = false
  }
}

watch(
  [filters, page, pageSize],
  () => {
    syncQuery()
    load()
  },
  { deep: true },
)

onMounted(load)

function resetFilters() {
  reset()
  load()
}

async function remove(row) {
  const ok = await confirm.ask(`确认删除隐患「${row.title}」？关联的整改跟踪记录会一并删除。`)
  if (!ok) return
  try {
    await deleteHazard(row.id)
    selected.value.delete(row.id)
    toast.success('隐患已删除')
    overview.invalidate()
    if (rows.value.length === 1 && page.value > 1) page.value -= 1
    else load()
  } catch (error) {
    toast.error(error.message)
  }
}

// ---------------------------------------------------------------------------
// 批量指派 / 批量催办
// ---------------------------------------------------------------------------

const batchMode = ref(null) // 'assign' | 'remind'
const batchRequestId = ref('')
const batchSubmitting = ref(false)
const batchResult = ref(null)

/** 每次发起批量操作生成一个幂等键：重复提交 / 网络重试不会产生重复记录 */
function newRequestId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  return `batch-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`
}

function openBatch(mode) {
  if (!selectedCount.value) return
  batchRequestId.value = newRequestId()
  batchMode.value = mode
}

async function submitBatch({ assignee, note, operator }) {
  const ids = [...selected.value.keys()]
  const mode = batchMode.value
  batchSubmitting.value = true
  try {
    const payload = {
      hazard_ids: ids,
      note: note || null,
      operator: operator || null,
      request_id: batchRequestId.value,
    }
    const result =
      mode === 'assign'
        ? await batchAssignHazards({ ...payload, assignee })
        : await batchRemindHazards(payload)

    // 对账：后端实际处理数量必须与跨页勾选数量一致
    const matched = result.processed_count === ids.length
    batchResult.value = {
      ok: matched,
      title: mode === 'assign' ? '批量指派结果' : '批量催办结果',
      lines: matched
        ? [`${result.message}，与勾选数量（${ids.length} 条）一致`]
        : [
            `勾选 ${ids.length} 条，实际处理 ${result.processed_count} 条，数量不一致，请刷新列表核对`,
            result.message,
          ],
    }
    if (matched) {
      clearSelection()
      batchMode.value = null
    }
    load()
    overview.invalidate()
  } catch (error) {
    // 后端保证整批原子性：失败即整批未生效，逐条原因直接展示
    batchResult.value = {
      ok: false,
      title: mode === 'assign' ? '批量指派未生效' : '批量催办未生效',
      lines: String(error.message).split('\n').filter(Boolean),
    }
    load()
  } finally {
    batchSubmitting.value = false
  }
}
</script>

<template>
  <div>
    <PageHeader title="隐患与整改" description="登记巡查发现的隐患，跟踪整改与验收销号全过程">
      <template #actions>
        <RouterLink class="btn btn-primary" to="/hazards/new">登记隐患</RouterLink>
      </template>
    </PageHeader>

    <HazardFilterBar v-model="filters" @reset="resetFilters" />

    <section class="card">
      <div v-if="selectedCount" class="batch-bar">
        <span>
          已选 <strong>{{ selectedCount }}</strong> 条<template v-if="selectedCount > rows.length">（含其他页勾选）</template>
        </span>
        <div class="row-gap">
          <button class="btn btn-sm btn-primary" type="button" @click="openBatch('assign')">
            批量指派责任人
          </button>
          <button class="btn btn-sm" type="button" @click="openBatch('remind')">批量催办</button>
          <button class="btn btn-sm" type="button" @click="clearSelection">清空选择</button>
        </div>
      </div>
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        :selectable="true"
        :selected-keys="selected"
        empty-text="没有符合条件的隐患"
        @toggle-row="toggleRow"
        @toggle-all="toggleAll"
      >
        <template #code="{ row }">
          <RouterLink class="mono" :to="`/hazards/${row.id}`">{{ row.code }}</RouterLink>
        </template>
        <template #title="{ row }">
          <RouterLink :to="`/hazards/${row.id}`">{{ row.title }}</RouterLink>
        </template>
        <template #reservoir="{ row }">
          <RouterLink v-if="row.reservoir" :to="`/reservoirs/${row.reservoir.id}`">
            {{ row.reservoir.name }}
          </RouterLink>
          <span v-else class="muted">—</span>
        </template>
        <template #category="{ row }">{{ dictionary.labelOf('structure_part', row.category) }}</template>
        <template #severity="{ row }">
          <StatusTag kind="hazard_severity" :value="row.severity" />
        </template>
        <template #status="{ row }">
          <StatusTag kind="hazard_status" :value="row.status" />
        </template>
        <template #discovered_on="{ row }">{{ formatDate(row.discovered_on) }}</template>
        <template #deadline="{ row }">
          <span class="nowrap">{{ formatDate(row.deadline) }}</span>
          <span v-if="row.is_overdue" class="tag tag-overdue" style="margin-left: 6px">逾期</span>
          <div class="timeline-meta">{{ deadlineHint(row.deadline, row.status === 'closed') }}</div>
        </template>
        <template #actions="{ row }">
          <div class="cell-actions">
            <RouterLink class="btn-link" :to="`/hazards/${row.id}`">跟踪</RouterLink>
            <RouterLink class="btn-link" :to="`/hazards/${row.id}/edit`">编辑</RouterLink>
            <button class="btn-link danger" type="button" @click="remove(row)">删除</button>
          </div>
        </template>
      </DataTable>
      <PaginationBar v-model:page="page" v-model:page-size="pageSize" :total="total" :pages="pages" />
    </section>

    <BatchActionDialog
      v-if="batchMode"
      :mode="batchMode"
      :count="selectedCount"
      :submitting="batchSubmitting"
      @submit="submitBatch"
      @close="batchMode = null"
    />
    <BatchResultDialog
      v-if="batchResult"
      :result="batchResult"
      @close="batchResult = null"
    />
  </div>
</template>
