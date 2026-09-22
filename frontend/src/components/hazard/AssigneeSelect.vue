<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fetchAssignees } from '@/api/hazards'

/**
 * 整改责任人选择：远程检索历史责任人，候选再多也不全量加载；
 * 同时允许直接输入尚未出现过的新名字。
 */
const model = defineModel({ type: String, default: '' })

const open = ref(false)
const options = ref([])
const loading = ref(false)
let timer = null
let requestSeq = 0

async function search(keyword) {
  const seq = (requestSeq += 1)
  loading.value = true
  try {
    const items = await fetchAssignees(keyword)
    if (seq === requestSeq) options.value = items // 丢弃过期响应，防止慢请求覆盖新结果
  } catch {
    if (seq === requestSeq) options.value = []
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

watch(model, (value) => {
  clearTimeout(timer)
  timer = setTimeout(() => search(value.trim()), 250)
})

onMounted(() => search(''))
onBeforeUnmount(() => clearTimeout(timer))

function pick(name) {
  model.value = name
  open.value = false
}

/** 延迟收起，让选项的 mousedown 先于 blur 触发 */
function onBlur() {
  setTimeout(() => {
    open.value = false
  }, 150)
}
</script>

<template>
  <div class="assignee-select">
    <input
      v-model="model"
      class="input"
      placeholder="输入姓名检索，或直接填写新责任人"
      autocomplete="off"
      @focus="open = true"
      @input="open = true"
      @blur="onBlur"
    />
    <div v-if="open" class="assignee-dropdown">
      <div v-if="loading" class="assignee-state">检索中…</div>
      <template v-else>
        <button
          v-for="item in options"
          :key="item.name"
          type="button"
          class="assignee-option"
          @mousedown.prevent="pick(item.name)"
        >
          <span>{{ item.name }}</span>
          <span class="assignee-count">未销号 {{ item.open_count }} 条</span>
        </button>
        <div v-if="!options.length" class="assignee-state">
          无匹配候选，可直接输入新责任人
        </div>
      </template>
    </div>
  </div>
</template>
