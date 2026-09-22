// 抽取结果 mock 数据（模拟真实招投标公告结构，非官方比赛数据）

// 列表页数据
export const mockExtractionList = [
  {
    announcementId: 'ANN-2026-001',
    projectName: '广州市增城区中心医院二期信息化建设项目',
    purchaser: '广州市增城区中心医院',
    winningSupplier: '广东智信信息技术有限公司',
    projectAmount: 4860000,
    productCount: 6,
    status: 'done',
    fileName: '增城区中心医院二期信息化建设项目招标公告.pdf'
  },
  {
    announcementId: 'ANN-2026-002',
    projectName: '政务服务中心智能自助终端采购项目',
    purchaser: '广州市政务服务数据管理局',
    winningSupplier: '广州数科系统集成有限公司',
    projectAmount: 1285000,
    productCount: 4,
    status: 'done',
    fileName: '政务服务中心智能自助终端采购公告.docx'
  },
  {
    announcementId: 'ANN-2026-003',
    projectName: '某高校实验实训室计算机设备采购项目',
    purchaser: '华南某职业技术学院',
    winningSupplier: '深圳创新教学设备有限公司',
    projectAmount: 862000,
    productCount: 3,
    status: 'extracting',
    fileName: '高校实验室设备采购项目公告.html'
  },
  {
    announcementId: 'ANN-2026-004',
    projectName: '区域全民健康信息平台建设项目',
    purchaser: '某市卫生健康局',
    winningSupplier: '待定',
    projectAmount: 3250000,
    productCount: 5,
    status: 'pending',
    fileName: '区域卫生信息平台建设项目公告.pdf'
  }
]

// 详情页数据：项目基本信息 + 产品实体抽取结果 + 原始文本
export const mockExtractionDetails = {
  'ANN-2026-001': {
    announcementId: 'ANN-2026-001',
    fileName: '增城区中心医院二期信息化建设项目招标公告.pdf',
    status: 'done',
    project: {
      projectName: '广州市增城区中心医院二期信息化建设项目',
      projectCode: 'GZZC-2026-XM001',
      purchaser: '广州市增城区中心医院',
      winningSuppliers: ['广东智信信息技术有限公司'],
      bidParticipants: ['广东智信信息技术有限公司', '广州慧康软件有限公司', '深圳卫宁医疗科技有限公司'],
      projectAmount: 4860000
    },
    products: [
      {
        productServiceName: '自助综合机',
        category: '医疗自助服务终端',
        brand: '钛金',
        specModel: 'TJ-8000A',
        unitPrice: 38500,
        quantity: 12,
        totalPrice: 462000
      },
      {
        productServiceName: '自助报到机',
        category: '医疗自助服务终端',
        brand: '钛金',
        specModel: 'TJ-5000B',
        unitPrice: 26800,
        quantity: 20,
        totalPrice: 536000
      },
      {
        productServiceName: '叫号显示器',
        category: '信息显示设备',
        brand: '讯鼎',
        specModel: 'XD-430T',
        unitPrice: 3200,
        quantity: 45,
        totalPrice: 144000
      },
      {
        productServiceName: '医院信息管理系统软件',
        category: '行业应用软件',
        brand: '慧康',
        specModel: 'HIS-V6.0',
        unitPrice: 1200000,
        quantity: 1,
        totalPrice: 1200000
      },
      {
        productServiceName: '服务器',
        category: '计算机设备',
        brand: '浪潮',
        specModel: 'NF5280M6',
        unitPrice: 68000,
        quantity: 4,
        totalPrice: 272000
      },
      {
        productServiceName: '交换机',
        category: '网络设备',
        brand: '华为',
        specModel: 'S5735-L48T4X',
        unitPrice: 9800,
        quantity: 8,
        totalPrice: 78400
      }
    ],
    rawText: `广州市增城区中心医院二期信息化建设项目 招标公告
项目编号：GZZC-2026-XM001
采购单位：广州市增城区中心医院

一、项目概况
本项目为广州市增城区中心医院二期信息化建设项目，主要采购内容包含医疗自助服务终端、信息显示设备、行业应用软件、计算机设备及网络设备等。

二、中标信息
中标供应商：广东智信信息技术有限公司
中标金额：人民币 486 万元整

三、投标参与方
1. 广东智信信息技术有限公司
2. 广州慧康软件有限公司
3. 深圳卫宁医疗科技有限公司

四、主要设备及报价明细
序号  产品名称            品牌  规格型号        单价(元)   数量   总价(元)
1    自助综合机          钛金  TJ-8000A        38,500    12     462,000
2    自助报到机          钛金  TJ-5000B        26,800    20     536,000
3    叫号显示器          讯鼎  XD-430T         3,200     45     144,000
4    医院信息管理系统软件 慧康  HIS-V6.0        1,200,000 1      1,200,000
5    服务器              浪潮  NF5280M6        68,000    4      272,000
6    交换机              华为  S5735-L48T4X    9,800     8      78,400`
  },
  'ANN-2026-002': {
    announcementId: 'ANN-2026-002',
    fileName: '政务服务中心智能自助终端采购公告.docx',
    status: 'done',
    project: {
      projectName: '政务服务中心智能自助终端采购项目',
      projectCode: 'ZWFW-2026-CG002',
      purchaser: '广州市政务服务数据管理局',
      winningSuppliers: ['广州数科系统集成有限公司'],
      bidParticipants: ['广州数科系统集成有限公司', '广东终端智造有限公司'],
      projectAmount: 1285000
    },
    products: [
      {
        productServiceName: '政务自助服务一体机',
        category: '自助服务终端',
        brand: '数科',
        specModel: 'SK-ZW300',
        unitPrice: 42500,
        quantity: 18,
        totalPrice: 765000
      },
      {
        productServiceName: '智能文件存取柜',
        category: '智能存储设备',
        brand: '数科',
        specModel: 'SK-GUI120',
        unitPrice: 18500,
        quantity: 6,
        totalPrice: 111000
      },
      {
        productServiceName: '排队叫号系统软件',
        category: '行业应用软件',
        brand: '数科',
        specModel: 'SK-PQ-V3',
        unitPrice: 280000,
        quantity: 1,
        totalPrice: 280000
      },
      {
        productServiceName: '高拍仪',
        category: '办公设备',
        brand: '良田',
        specModel: 'S1500A3',
        unitPrice: 1600,
        quantity: 80,
        totalPrice: 128000
      }
    ],
    rawText: `政务服务中心智能自助终端采购项目 采购公告
项目编号：ZWFW-2026-CG002
采购单位：广州市政务服务数据管理局

一、采购内容
政务自助服务一体机、智能文件存取柜、排队叫号系统软件、高拍仪等。

二、成交信息
成交供应商：广州数科系统集成有限公司
成交金额：人民币 128.5 万元整

三、参与供应商
1. 广州数科系统集成有限公司
2. 广东终端智造有限公司`
  }
}

// 按 ID 获取详情（mock）
export function getExtractionDetail(id) {
  return mockExtractionDetails[id] || null
}
