// 关系分析 mock 数据（模拟供应商/采购单位/投标参与方关系，非官方比赛数据）
// 后续接入 Neo4j 后，从这里替换为后端接口返回的 nodes / links

export const mockRelationGraph = {
  nodes: [
    // type: purchaser=采购单位, supplier=中标供应商, participant=投标参与方
    { id: 'p1', name: '广州市增城区中心医院', type: 'purchaser' },
    { id: 'p2', name: '广州市政务服务数据管理局', type: 'purchaser' },
    { id: 's1', name: '广东智信信息技术有限公司', type: 'supplier' },
    { id: 's2', name: '广州数科系统集成有限公司', type: 'supplier' },
    { id: 's3', name: '广州慧康软件有限公司', type: 'participant' },
    { id: 's4', name: '深圳卫宁医疗科技有限公司', type: 'participant' },
    { id: 's5', name: '广东终端智造有限公司', type: 'participant' }
  ],
  links: [
    // source -> target，label 为关系
    { source: 'p1', target: 's1', label: '中标' },
    { source: 'p2', target: 's2', label: '中标' },
    { source: 's1', target: 's3', label: '共同投标' },
    { source: 's1', target: 's4', label: '共同投标' },
    { source: 's2', target: 's5', label: '共同投标' }
  ]
}

export const NODE_TYPE_STYLE = {
  purchaser: { color: '#409eff', label: '采购单位' },
  supplier: { color: '#67c23a', label: '中标供应商' },
  participant: { color: '#e6a23c', label: '投标参与方' }
}

// 简单的关系查询 mock：按关键词过滤节点
export function searchRelations(keyword) {
  if (!keyword) return mockRelationGraph
  const kw = keyword.trim()
  const nodes = mockRelationGraph.nodes.filter(n => n.name.includes(kw))
  if (nodes.length === 0) return { nodes: [], links: [] }
  const ids = new Set(nodes.map(n => n.id))
  const links = mockRelationGraph.links.filter(l => ids.has(l.source) || ids.has(l.target))
  // 补充与命中节点相连的对方节点
  links.forEach(l => {
    ids.add(l.source)
    ids.add(l.target)
  })
  const allNodes = mockRelationGraph.nodes.filter(n => ids.has(n.id))
  return { nodes: allNodes, links }
}
