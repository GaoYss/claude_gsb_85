<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { deleteHazard, fetchHazards } from '@/api/hazards'
import DataTable from '@/components/common/DataTable.vue'
import PaginationBar from '@/components/common/PaginationBar.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import BatchOperationDialog from '@/components/hazard/BatchOperationDialog.vue'
import HazardFilterBar from '@/components/hazard/HazardFilterBar.vue'
import { useListQuery } from '@/composables/useListQuery'
import { useConfirmStore } from '@/stores/confirm'
import { useDictionaryStore } from '@/stores/dictionary'
import { useToastStore } from '@/stores/toast'
import { deadlineHint, formatDate } from '@/utils/format'

const dictionary = useDictionaryStore()
const toast = useToastStore()
const confirm = useConfirmStore()

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

// 跨页勾选：selectedIds 保存全部已选 id（翻页 / 改筛选不清空）；
// rowCache 缓存每一页的行快照，提交与失败明细展示时按 id 取标题
const selectedIds = ref([])
const rowCache = new Map()
const dialogMode = ref('') // '' | 'assign' | 'urge'

const selectedRows = computed(() =>
  selectedIds.value.map((id) => rowCache.get(id)).filter(Boolean),
)

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
    for (const item of data.items) rowCache.set(item.id, item)
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

function clearSelection() {
  selectedIds.value = []
}

function onBatchDone(result) {
  const actionLabel = result.action === 'assign' ? '指派' : '催办'
  if (result.already_processed) {
    // 同一批次重复提交：后端已按幂等键去重，明确告知未重复写入
    toast.info(`该批次此前已处理过（${result.processed} 条），未重复${actionLabel}`)
  } else {
    // processed 与勾选数量一致才提示成功；不一致属于异常，如实提示
    if (result.processed === selectedIds.value.length) {
      toast.success(`批量${actionLabel}完成：${result.processed} 条已处理`)
    } else {
      toast.error(
        `批量${actionLabel}数量异常：勾选 ${selectedIds.value.length} 条，实际处理 ${result.processed} 条，请核对`,
      )
    }
  }
  dialogMode.value = ''
  clearSelection()
  load() // 列表立即刷新；详情页与首页待办均为进入时实时拉取，天然同步
}

async function remove(row) {
  const ok = await confirm.ask(`确认删除隐患「${row.title}」？关联的整改跟踪记录会一并删除。`)
  if (!ok) return
  try {
    await deleteHazard(row.id)
    toast.success('隐患已删除')
    selectedIds.value = selectedIds.value.filter((id) => id !== row.id)
    rowCache.delete(row.id)
    if (rows.value.length === 1 && page.value > 1) page.value -= 1
    else load()
  } catch (error) {
    toast.error(error.message)
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
      <div v-if="selectedIds.length" class="batch-toolbar">
        <span class="batch-toolbar-count">
          已选 <strong>{{ selectedIds.length }}</strong> 条（跨页勾选累计）
        </span>
        <div class="batch-toolbar-actions">
          <button class="btn btn-sm btn-primary" type="button" @click="dialogMode = 'assign'">
            批量指派责任人
          </button>
          <button class="btn btn-sm" type="button" @click="dialogMode = 'urge'">批量催办</button>
          <button class="btn-link" type="button" @click="clearSelection">清空选择</button>
        </div>
      </div>

      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        selectable
        :selected="selectedIds"
        empty-text="没有符合条件的隐患"
        @update:selected="selectedIds = $event"
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

    <BatchOperationDialog
      v-if="dialogMode"
      :mode="dialogMode"
      :rows="selectedRows"
      @close="dialogMode = ''"
      @done="onBatchDone"
    />
  </div>
</template>
