import request from './request'

// 文件上传与解析相关接口（当前未接后端，页面使用 mock 演示）
// 后端就绪后，取消注释并替换 Upload.vue 中的 mock 逻辑即可

// POST /api/upload —— 上传文件
export function uploadFile(formData) {
  return request.post('/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

// POST /api/parse/{file_id} —— 触发文件解析
export function parseFile(fileId) {
  return request.post(`/parse/${fileId}`)
}

// POST /api/extract/{file_id} —— 触发 AI 实体抽取
export function extractFile(fileId) {
  return request.post(`/extract/${fileId}`)
}
