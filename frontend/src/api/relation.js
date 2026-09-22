import request from './request'

// 关系分析相关接口（当前未接后端，页面使用 mock 演示）
// 后续接入 Neo4j 后在此扩展

// GET 关系图数据（预留接口，具体路径以后端为准）
export function getRelationGraph(params) {
  return request.get('/relations', { params })
}
