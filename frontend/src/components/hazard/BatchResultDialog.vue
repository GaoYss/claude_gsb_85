<script setup>
defineProps({
  result: { type: Object, required: true }, // { ok, title, lines: [] }
})

const emit = defineEmits(['close'])
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-header">{{ result.title }}</div>
      <div class="modal-body">
        <div
          class="batch-result-banner"
          :class="result.ok ? 'is-ok' : 'is-fail'"
        >
          {{ result.ok ? '批量操作已生效' : '整批未生效，未改动任何隐患' }}
        </div>
        <ul class="batch-result-lines">
          <li v-for="(line, index) in result.lines" :key="index">{{ line }}</li>
        </ul>
      </div>
      <div class="modal-footer">
        <button class="btn btn-primary" @click="emit('close')">知道了</button>
      </div>
    </div>
  </div>
</template>
