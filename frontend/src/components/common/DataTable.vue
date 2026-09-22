<script setup>
import { computed } from 'vue'

const props = defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  rowKey: { type: String, default: 'id' },
  emptyText: { type: String, default: '暂无数据' },
  /** 开启后展示勾选列；selected 为已选行 key 数组，跨页保留由父组件负责 */
  selectable: { type: Boolean, default: false },
  selected: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:selected'])

const selectedSet = computed(() => new Set(props.selected))
const pageKeys = computed(() => props.rows.map((row) => row[props.rowKey]))
const allChecked = computed(
  () => pageKeys.value.length > 0 && pageKeys.value.every((key) => selectedSet.value.has(key)),
)
const partiallyChecked = computed(
  () => !allChecked.value && pageKeys.value.some((key) => selectedSet.value.has(key)),
)
const stateColspan = computed(() => props.columns.length + (props.selectable ? 1 : 0))

/** 表头勾选只作用于当前页：全选本页 / 取消本页，不影响其他页的已选 */
function togglePage() {
  if (allChecked.value) {
    const pageSet = new Set(pageKeys.value)
    emit(
      'update:selected',
      props.selected.filter((key) => !pageSet.has(key)),
    )
  } else {
    const merged = new Set(props.selected)
    for (const key of pageKeys.value) merged.add(key)
    emit('update:selected', [...merged])
  }
}

function toggleRow(key) {
  if (selectedSet.value.has(key)) {
    emit(
      'update:selected',
      props.selected.filter((item) => item !== key),
    )
  } else {
    emit('update:selected', [...props.selected, key])
  }
}
</script>

<template>
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th v-if="selectable" class="check-cell">
            <input
              type="checkbox"
              :checked="allChecked"
              :indeterminate.prop="partiallyChecked"
              aria-label="全选本页"
              @change="togglePage"
            />
          </th>
          <th v-for="column in columns" :key="column.key" :style="column.width ? { width: column.width } : null">
            {{ column.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading">
          <td class="table-state" :colspan="stateColspan">加载中…</td>
        </tr>
        <tr v-else-if="!rows.length">
          <td class="table-state" :colspan="stateColspan">{{ emptyText }}</td>
        </tr>
        <tr v-else v-for="row in rows" :key="row[rowKey]">
          <td v-if="selectable" class="check-cell">
            <input
              type="checkbox"
              :checked="selectedSet.has(row[rowKey])"
              :aria-label="`选择 ${row[rowKey]}`"
              @change="toggleRow(row[rowKey])"
            />
          </td>
          <td v-for="column in columns" :key="column.key">
            <slot :name="column.key" :row="row">
              {{ row[column.key] === null || row[column.key] === undefined || row[column.key] === '' ? '—' : row[column.key] }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
