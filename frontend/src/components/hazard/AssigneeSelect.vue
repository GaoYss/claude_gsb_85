<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'

import { fetchAssigneeCandidates } from '@/api/hazards'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '输入姓名检索，或直接填写新责任人' },
})

const emit = defineEmits(['update:modelValue'])

const keyword = ref(props.modelValue)
const candidates = ref([])
const open = ref(false)
const searching = ref(false)
let timer = null

watch(
  () => props.modelValue,
  (value) => {
    keyword.value = value
  },
)

async function search(value) {
  searching.value = true
  try {
    candidates.value = await fetchAssigneeCandidates(value)
  } catch {
    candidates.value = []
  } finally {
    searching.value = false
  }
}

function onInput(event) {
  const value = event.target.value
  keyword.value = value
  emit('update:modelValue', value)
  open.value = true
  clearTimeout(timer)
  // 防抖检索：候选责任人很多时，每次输入都走服务端过滤而不是全量下拉
  timer = setTimeout(() => search(value.trim()), 300)
}

function onFocus() {
  open.value = true
  search(keyword.value.trim())
}

function onBlur() {
  // 延迟收起，让选项点击事件先触发
  setTimeout(() => {
    open.value = false
  }, 150)
}

function pick(name) {
  keyword.value = name
  emit('update:modelValue', name)
  open.value = false
}

onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="combo">
    <input
      class="input"
      :value="keyword"
      :placeholder="placeholder"
      autocomplete="off"
      @input="onInput"
      @focus="onFocus"
      @blur="onBlur"
    />
    <div v-if="open" class="combo-panel">
      <div v-if="searching" class="combo-state">检索中…</div>
      <template v-else>
        <button
          v-for="name in candidates"
          :key="name"
          type="button"
          class="combo-option"
          :class="{ 'is-active': name === keyword }"
          @mousedown.prevent="pick(name)"
        >
          {{ name }}
        </button>
        <div v-if="!candidates.length" class="combo-state">
          无匹配候选人，可直接输入新姓名
        </div>
      </template>
    </div>
  </div>
</template>
