<script setup>
import { computed } from 'vue'

const props = defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  rowKey: { type: String, default: 'id' },
  emptyText: { type: String, default: '暂无数据' },
  /** 开启多选：选择集由父组件跨页持有（Set / Map，用 has 判断） */
  selectable: { type: Boolean, default: false },
  selectedKeys: { type: Object, default: () => new Set() },
})

const emit = defineEmits(['toggle-row', 'toggle-all'])

const columnCount = computed(() => props.columns.length + (props.selectable ? 1 : 0))

const pageAllChecked = computed(
  () =>
    props.rows.length > 0 &&
    props.rows.every((row) => props.selectedKeys.has(row[props.rowKey])),
)
const pagePartlyChecked = computed(
  () =>
    !pageAllChecked.value &&
    props.rows.some((row) => props.selectedKeys.has(row[props.rowKey])),
)
</script>

<template>
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th v-if="selectable" class="select-cell">
            <input
              type="checkbox"
              :checked="pageAllChecked"
              :indeterminate.prop="pagePartlyChecked"
              aria-label="全选当前页"
              @change="emit('toggle-all', rows)"
            />
          </th>
          <th v-for="column in columns" :key="column.key" :style="column.width ? { width: column.width } : null">
            {{ column.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading">
          <td class="table-state" :colspan="columnCount">加载中…</td>
        </tr>
        <tr v-else-if="!rows.length">
          <td class="table-state" :colspan="columnCount">{{ emptyText }}</td>
        </tr>
        <tr
          v-else
          v-for="row in rows"
          :key="row[rowKey]"
          :class="{ 'is-selected': selectable && selectedKeys.has(row[rowKey]) }"
        >
          <td v-if="selectable" class="select-cell">
            <input
              type="checkbox"
              :checked="selectedKeys.has(row[rowKey])"
              :aria-label="`选择 ${row[rowKey]}`"
              @change="emit('toggle-row', row)"
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
