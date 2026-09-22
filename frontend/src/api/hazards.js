import { compact, http } from './http'

export function fetchHazards(params) {
  return http.get('/hazards', { params: compact(params) })
}

export function fetchHazard(id) {
  return http.get(`/hazards/${id}`)
}

export function createHazard(payload) {
  return http.post('/hazards', payload)
}

export function updateHazard(id, payload) {
  return http.put(`/hazards/${id}`, payload)
}

export function deleteHazard(id) {
  return http.delete(`/hazards/${id}`)
}

export function addRectification(id, payload) {
  return http.post(`/hazards/${id}/rectifications`, payload)
}

export function transitionHazard(id, payload) {
  return http.post(`/hazards/${id}/transition`, payload)
}

/** 整改责任人候选（远程检索，候选人多时不全量加载） */
export function fetchAssignees(keyword = '') {
  return http.get('/hazards/assignees', { params: compact({ keyword }) })
}

/** 批量指派整改责任人。batch_id 由调用方生成，用于幂等防重 */
export function batchAssignHazards(payload) {
  return http.post('/hazards/batch-assign', payload)
}

/** 批量催办。同样按 batch_id 幂等 */
export function batchUrgeHazards(payload) {
  return http.post('/hazards/batch-urge', payload)
}

