<script setup>
import { computed, ref } from 'vue'

import { batchAssignHazards, batchUrgeHazards } from '@/api/hazards'
import AssigneeSelect from '@/components/hazard/AssigneeSelect.vue'
import { useToastStore } from '@/stores/toast'
import { newBatchId } from '@/utils/form'

const props = defineProps({
  /** assign = 批量指派责任人；urge = 批量催办 */
  mode: { type: String, required: true },
  /** 选中的隐患快照（含跨页），用于展示与提交 */
  rows: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'done'])

const toast = useToastStore()

const isAssign = computed(() => props.mode === 'assign')
const title = computed(() => (isAssign.value ? '批量指派整改责任人' : '批量催办'))

const assignee = ref('')
const content = ref('')
const operator = ref('')
const submitting = ref(false)
/** 409 时后端返回的逐条失败明细；整批未生效，弹窗内展示供用户修正 */
const failures = ref([])

// 幂等键：弹窗每次打开生成一次。提交失败（未落库）重试沿用同一个；
// 提交成功后弹窗即关闭，同一批次的重复提交由后端按 batch_id 去重。
const batchId = newBatchId()

const canSubmit = computed(() => {
  if (submitting.value || !props.rows.length) return false
  return isAssign.value ? assignee.value.trim().length > 0 : true
})

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  failures.value = []
  const payload = {
    hazard_ids: props.rows.map((row) => row.id),
    operator: operator.value.trim() || undefined,
    batch_id: batchId,
  }
  try {
    const result = isAssign.value
      ? await batchAssignHazards({ ...payload, assignee: assignee.value.trim() })
      : await batchUrgeHazards({ ...payload, content: content.value.trim() || undefined })
    emit('done', result)
  } catch (error) {
    if (error.status === 409 && Array.isArray(error.data?.failures)) {
      // 整批未生效：逐条列出原因，用户修正选择后可重新提交
      failures.value = error.data.failures
    } else {
      toast.error(error.message)
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal batch-modal" role="dialog" aria-modal="true">
      <div class="modal-header">
        {{ title }}
        <span class="muted batch-count">已选 {{ rows.length }} 条（含跨页勾选）</span>
      </div>
      <div class="modal-body">
        <div v-if="failures.length" class="batch-failures">
          <div class="batch-failures-title">
            整批未生效：以下 {{ failures.length }} 条不满足条件，任何一条都未处理。
            请取消勾选后重新提交，或到详情页单独处理。
          </div>
          <ul>
            <li v-for="item in failures" :key="item.hazard_id">
              <span class="mono">{{ item.code || `#${item.hazard_id}` }}</span>
              <span v-if="item.title" class="batch-failure-title">{{ item.title }}</span>
              <span class="batch-failure-reason">{{ item.reason }}</span>
            </li>
          </ul>
        </div>

        <template v-if="isAssign">
          <label class="batch-field">
            <span>整改责任人 <em class="required">*</em></span>
            <AssigneeSelect v-model="assignee" />
          </label>
        </template>
        <template v-else>
          <label class="batch-field">
            <span>催办说明</span>
            <textarea
              v-model="content"
              class="input"
              rows="3"
              maxlength="200"
              placeholder="可空，默认按各隐患责任人生成催办内容"
            ></textarea>
          </label>
        </template>

        <label class="batch-field">
          <span>操作人</span>
          <input v-model="operator" class="input" maxlength="64" placeholder="记录到整改流水，可空" />
        </label>
      </div>
      <div class="modal-footer">
        <button class="btn" type="button" :disabled="submitting" @click="emit('close')">取消</button>
        <button class="btn btn-primary" type="button" :disabled="!canSubmit" @click="submit">
          {{ submitting ? '提交中…' : `确认${isAssign ? '指派' : '催办'} ${rows.length} 条` }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.batch-modal {
  max-width: 520px;
}

.batch-count {
  font-size: 12px;
  font-weight: 400;
  margin-left: 8px;
}

.batch-field {
  display: block;
  margin-bottom: 14px;
}

.batch-field > span {
  display: block;
  font-size: 13px;
  margin-bottom: 6px;
  color: var(--muted);
}

.required {
  color: var(--danger, #b91c1c);
  font-style: normal;
}

.batch-failures {
  border: 1px solid var(--danger, #b91c1c);
  border-radius: var(--radius, 8px);
  padding: 10px 12px;
  margin-bottom: 14px;
  font-size: 13px;
  background: rgba(185, 28, 28, 0.06);
}

.batch-failures-title {
  font-weight: 600;
  margin-bottom: 8px;
}

.batch-failures ul {
  margin: 0;
  padding-left: 4px;
  list-style: none;
  max-height: 180px;
  overflow: auto;
}

.batch-failures li {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 3px 0;
}

.batch-failure-title {
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}

.batch-failure-reason {
  color: var(--danger, #b91c1c);
}
</style>
