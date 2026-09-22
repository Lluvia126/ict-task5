// 首页工作台 mock 数据
// 注意：当前为模拟数据，非官方比赛数据，后续接入后端后替换

export const mockStats = {
  fileCount: 12,          // 数据文件数量
  announcementCount: 8,   // 已处理公告数量
  entityCount: 156,       // 已抽取实体数量
  supplierCount: 23,      // 供应商数量
  productCount: 47,       // 产品数量
  relationCount: 5        // 关系数量（采购单位-供应商-参与方关系边数）
}

export const mockRecentRecords = [
  {
    id: 1,
    fileName: '增城区中心医院二期信息化建设项目招标公告.pdf',
    announcementId: 'ANN-2026-001',
    status: 'done',
    time: '2026-09-20 14:32'
  },
  {
    id: 2,
    fileName: '政务服务中心智能自助终端采购公告.docx',
    announcementId: 'ANN-2026-002',
    status: 'done',
    time: '2026-09-20 15:10'
  },
  {
    id: 3,
    fileName: '高校实验室设备采购项目公告.html',
    announcementId: 'ANN-2026-003',
    status: 'extracting',
    time: '2026-09-20 16:05'
  },
  {
    id: 4,
    fileName: '区域卫生信息平台建设项目公告.pdf',
    announcementId: 'ANN-2026-004',
    status: 'pending',
    time: '2026-09-20 17:40'
  }
]

export const mockSystemStatus = {
  aiService: '正常',       // AI 抽取服务状态
  database: '正常',        // 数据库状态
  graphService: '未接入'   // 知识图谱服务状态
}
