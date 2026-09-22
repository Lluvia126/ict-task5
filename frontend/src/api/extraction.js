import request from './request'

// 抽取结果相关接口（当前未接后端，页面使用 mock 演示）

// GET /api/extractions —— 抽取结果列表
export function getExtractions() {
  return request.get('/extractions')
}

// GET /api/extractions/{announcement_id} —— 抽取结果详情
export function getExtractionDetail(announcementId) {
  return request.get(`/extractions/${announcementId}`)
}

// GET /api/ai/health —— AI 服务健康检查
export function getAiHealth() {
  return request.get('/ai/health')
}
