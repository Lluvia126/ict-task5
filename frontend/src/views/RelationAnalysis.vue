<template>
  <div>
    <!-- 查询区 -->
    <div class="page-card section">
      <div class="page-title">关系查询</div>
      <el-form inline>
        <el-form-item label="供应商">
          <el-input v-model="supplierKeyword" placeholder="输入供应商名称" clearable style="width: 220px" />
        </el-form-item>
        <el-form-item label="采购单位">
          <el-input v-model="purchaserKeyword" placeholder="输入采购单位名称" clearable style="width: 220px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">查询关系</el-button>
          <el-button @click="onReset">重置</el-button>
        </el-form-item>
      </el-form>
      <div class="query-tip">
        可查询：供应商 - 采购单位 之间的中标关系；供应商与供应商之间的共同投标关系。
      </div>
    </div>

    <!-- 关系图展示区域 -->
    <div class="page-card section">
      <div class="page-title">关系图</div>
      <div class="legend">
        <span v-for="(style, type) in NODE_TYPE_STYLE" :key="type" class="legend-item">
          <span class="legend-dot" :style="{ backgroundColor: style.color }"></span>
          {{ style.label }}
        </span>
      </div>

      <div class="graph-area" v-if="graph.nodes.length">
        <svg :viewBox="viewBox" width="100%" height="420">
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
              :cx="nodePos(node.id).x" :cy="nodePos(node.id).y" r="26"
              :fill="NODE_TYPE_STYLE[node.type].color" fill-opacity="0.9"
            />
            <text
              :x="nodePos(node.id).x" :y="nodePos(node.id).y + 4"
              text-anchor="middle" font-size="12" fill="#fff"
            >{{ node.name.slice(0, 4) }}</text>
            <text
              :x="nodePos(node.id).x" :y="nodePos(node.id).y + 42"
              text-anchor="middle" font-size="12" fill="#303133"
            >{{ node.name }}</text>
          </g>
        </svg>
      </div>
      <el-empty v-else description="暂无关系数据，请输入关键词查询" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  mockRelationGraph, NODE_TYPE_STYLE, searchRelations
} from '../mock/mockRelations'

const supplierKeyword = ref('')
const purchaserKeyword = ref('')

// 当前展示完整 mock 关系图；点击查询后按关键词过滤
const graph = ref(mockRelationGraph)

const viewBox = computed(() => '0 0 900 420')

// 固定布局：采购单位一行，供应商/参与方分行展开（后续接入 Neo4j 后可替换为力导向图）
const LAYOUT = { p1: [200, 90], p2: [650, 90], s1: [300, 250], s2: [700, 250], s3: [140, 370], s4: [420, 370], s5: [760, 370] }

function nodePos(id) {
  return { x: LAYOUT[id]?.x ?? 450, y: LAYOUT[id]?.y ?? 210 }
}

function onSearch() {
  const kw = supplierKeyword.value || purchaserKeyword.value
  if (!kw) {
    ElMessage.warning('请输入供应商或采购单位关键词')
    return
  }
  graph.value = searchRelations(kw)
  if (graph.value.nodes.length === 0) {
    ElMessage.info('未找到匹配的关系数据')
  }
}

function onReset() {
  supplierKeyword.value = ''
  purchaserKeyword.value = ''
  graph.value = mockRelationGraph
}
</script>

<style scoped>
.section {
  margin-bottom: 16px;
}

.query-tip {
  font-size: 12px;
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
  background: #fafbfc;
}
</style>
