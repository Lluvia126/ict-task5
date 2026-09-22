<template>
  <div>
    <!-- 工作台小标题 + 抽取入口 -->
    <div class="page-header">
      <div>
        <div class="page-header-title">数据智能工作台</div>
        <div class="page-header-sub">招投标公告实体挖掘与关系建模概览</div>
      </div>
      <el-button type="primary" @click="$router.push('/upload')">
        <el-icon style="margin-right: 6px"><Upload /></el-icon>
        开始数据抽取
      </el-button>
    </div>

    <!-- 核心统计卡片 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :xs="12" :sm="8" :md="4" v-for="card in statCards" :key="card.title">
        <StatCard v-bind="card" />
      </el-col>
    </el-row>

    <!-- 知识图谱服务总览（核心视觉区域） -->
    <div class="page-card graph-card">
      <div class="card-head">
        <div class="page-title">知识图谱服务总览</div>
        <span class="card-sub">采购单位、供应商、投标参与方及项目关系概览</span>
      </div>
      <div class="legend">
        <span v-for="(style, type) in NODE_TYPE_STYLE" :key="type" class="legend-item">
          <span class="legend-dot" :style="{ backgroundColor: style.color }"></span>
          {{ style.label }}
        </span>
      </div>
      <div class="graph-area">
        <svg :viewBox="`0 0 900 360`" width="100%" height="360" preserveAspectRatio="xMidYMid meet">
          <!-- 连线 -->
          <g v-for="(link, i) in graph.links" :key="'l' + i">
            <line
              :x1="nodePos(link.source).x" :y1="nodePos(link.source).y"
              :x2="nodePos(link.target).x" :y2="nodePos(link.target).y"
              stroke="#c0c4cc" stroke-width="1.5"
            />
            <text
              :x="(nodePos(link.source).x + nodePos(link.target).x) / 2"
              :y="(nodePos(link.source).y + nodePos(link.target).y) / 2 - 6"
              text-anchor="middle" font-size="11" fill="#909399"
            >{{ link.label }}</text>
          </g>
          <!-- 节点 -->
          <g v-for="node in graph.nodes" :key="node.id" class="node">
            <circle
              :cx="nodePos(node.id).x" :cy="nodePos(node.id).y" r="28"
              :fill="NODE_TYPE_STYLE[node.type].color" fill-opacity="0.9"
            />
            <text
              :x="nodePos(node.id).x" :y="nodePos(node.id).y + 4"
              text-anchor="middle" font-size="12" fill="#fff"
            >{{ node.name.slice(0, 4) }}</text>
            <text
              :x="nodePos(node.id).x" :y="nodePos(node.id).y + 46"
              text-anchor="middle" font-size="12" fill="#303133"
            >{{ node.name }}</text>
          </g>
        </svg>
      </div>
    </div>

    <!-- 辅助信息：最近处理记录 + 系统状态 -->
    <el-row :gutter="16" class="content-row">
      <el-col :xs="24" :md="16">
        <div class="page-card">
          <div class="page-title">最近处理记录</div>
          <el-table :data="recentRecords" stripe>
            <el-table-column prop="fileName" label="文件名" min-width="220" show-overflow-tooltip />
            <el-table-column prop="announcementId" label="公告编号" width="130" />
            <el-table-column label="状态" width="110" align="center">
              <template #default="{ row }">
                <StatusTag :status="row.status" />
              </template>
            </el-table-column>
            <el-table-column prop="time" label="时间" width="150" />
          </el-table>
        </div>
      </el-col>

      <el-col :xs="24" :md="8">
        <div class="page-card">
          <div class="page-title">系统运行状态</div>
          <div v-for="item in statusItems" :key="item.name" class="status-item">
            <span>{{ item.name }}</span>
            <el-tag :type="item.type" size="small">{{ item.value }}</el-tag>
          </div>
          <el-divider />
          <div class="status-tip">数据说明：当前展示为模拟数据，用于演示系统流程</div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Upload } from '@element-plus/icons-vue'
import StatCard from '../components/StatCard.vue'
import StatusTag from '../components/StatusTag.vue'
import { mockStats, mockRecentRecords, mockSystemStatus } from '../mock/mockDashboard'
import { mockRelationGraph, NODE_TYPE_STYLE } from '../mock/mockRelations'

// 统计卡片：含新增的“关系数量”
const statCards = computed(() => [
  { title: '数据文件数量', value: mockStats.fileCount, icon: 'Files', color: '#409eff' },
  { title: '已处理公告', value: mockStats.announcementCount, icon: 'Document', color: '#67c23a' },
  { title: '已抽取实体', value: mockStats.entityCount, icon: 'Collection', color: '#e6a23c' },
  { title: '供应商数量', value: mockStats.supplierCount, icon: 'OfficeBuilding', color: '#f56c6c' },
  { title: '产品数量', value: mockStats.productCount, icon: 'ShoppingCart', color: '#909399' },
  { title: '关系数量', value: mockStats.relationCount, icon: 'Share', color: '#9b59b6' }
])

const recentRecords = mockRecentRecords

const graph = mockRelationGraph

// 知识图谱布局：采购单位在顶部，中标供应商在中部，投标参与方在底部
const LAYOUT = {
  p1: [200, 70], p2: [680, 70],
  s1: [300, 200], s2: [620, 200],
  s3: [130, 320], s4: [430, 320], s5: [760, 320]
}

function nodePos(id) {
  const p = LAYOUT[id]
  return { x: p ? p[0] : 450, y: p ? p[1] : 180 }
}

const statusItems = computed(() => [
  { name: 'AI 抽取服务', value: mockSystemStatus.aiService, type: 'success' },
  { name: '数据库服务', value: mockSystemStatus.database, type: 'success' },
  { name: '知识图谱服务', value: mockSystemStatus.graphService, type: 'info' }
])
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-header-title {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}

.page-header-sub {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.stat-row {
  margin-bottom: 16px;
}

.stat-row :deep(.el-col) {
  margin-bottom: 12px;
}

.graph-card {
  margin-bottom: 16px;
}

.card-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 12px;
}

.card-sub {
  font-size: 13px;
  color: #909399;
}

.legend {
  display: flex;
  gap: 20px;
  margin-bottom: 12px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #606266;
}

.legend-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  display: inline-block;
}

.graph-area {
  border: 1px dashed #dcdfe6;
  border-radius: 8px;
  background: linear-gradient(180deg, #fafbfc 0%, #f5f7fa 100%);
}

.content-row :deep(.el-col) {
  margin-bottom: 16px;
}

.status-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  font-size: 14px;
  color: #606266;
}

.status-tip {
  font-size: 12px;
  color: #c0c4cc;
}
</style>
