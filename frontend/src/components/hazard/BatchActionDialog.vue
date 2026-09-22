<script setup>
import { computed, reactive, ref } from 'vue'

import AssigneeSelect from '@/components/hazard/AssigneeSelect.vue'

const props = defineProps({
  mode: { type: String, required: true }, // assign | remind
  count: { type: Number, required: true },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['submit', 'close'])

const form = reactive({ assignee: '', note: '', operator: '' })
const error = ref('')

const isAssign = computed(() => props.mode === 'assign')
const title = computed(() => (isAssign.value ? '批量指派整改责任人' : '批量催办'))

function submit() {
  if (isAssign.value && !form.assignee.trim()) {
    error.value = '请选择或输入整改责任人'
    return
  }
  error.value = ''
  emit('submit', {
    assignee: form.assignee.trim(),
    note: form.note.trim(),
    operator: form.operator.trim(),
  })
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-header">{{ title }}</div>
      <div class="modal-body">
        <p class="muted" style="margin: 0 0 12px">
          将对已勾选的 <strong>{{ count }}</strong> 条隐患执行{{ isAssign ? '指派' : '催办' }}，
          每条都会写入整改跟踪记录；若其中有不满足条件的隐患，整批不会生效。
        </p>
        <div v-if="isAssign" class="field" style="margin-bottom: 12px">
          <label>整改责任人 <span class="required">*</span></label>
          <AssigneeSelect v-model="form.assignee" />
        </div>
        <p v-else class="muted" style="margin: 0 0 12px; font-size: 12px">
          将按各隐患当前登记的整改责任人逐条生成催办提醒；未指派责任人的隐患按「整改责任人」统称。
        </p>
        <div class="field" style="margin-bottom: 12px">
          <label>{{ isAssign ? '备注（可选）' : '催办说明（可选）' }}</label>
          <textarea
            v-model="form.note"
            class="textarea"
            style="min-height: 60px"
            :placeholder="isAssign ? '会一并写入整改跟踪记录' : '留空则使用默认催办内容'"
          />
        </div>
        <div class="field">
          <label>操作人（可选）</label>
          <input v-model="form.operator" class="input" placeholder="记录到整改流水，例如 管理员" />
        </div>
        <p v-if="error" style="color: var(--danger); margin: 8px 0 0; font-size: 13px">{{ error }}</p>
      </div>
      <div class="modal-footer">
        <button class="btn" :disabled="submitting" @click="emit('close')">取消</button>
        <button class="btn btn-primary" :disabled="submitting" @click="submit">
          {{ submitting ? '提交中…' : `确认${isAssign ? '指派' : '催办'} ${count} 条` }}
        </button>
      </div>
    </div>
  </div>
</template>
