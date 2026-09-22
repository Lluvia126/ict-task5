<template>
  <div v-if="detail">
    <!-- 项目基本信息 -->
    <div class="page-card section">
      <div class="page-title">项目基本信息</div>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="公告编号">{{ detail.announcementId }}</el-descriptions-item>
        <el-descriptions-item label="来源文件">{{ detail.fileName }}</el-descriptions-item>
        <el-descriptions-item label="项目名称">{{ detail.project.projectName }}</el-descriptions-item>
        <el-descriptions-item label="项目编号">{{ detail.project.projectCode }}</el-descriptions-item>
        <el-descriptions-item label="采购单位">{{ detail.project.purchaser }}</el-descriptions-item>
        <el-descriptions-item label="项目金额">{{ formatAmount(detail.project.projectAmount) }}</el-descriptions-item>
        <el-descriptions-item label="中标供应商" :span="2">
          <el-tag v-for="s in detail.project.winningSuppliers" :key="s" type="success" style="margin-right: 8px">
            {{ s }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="投标参与方" :span="2">
          <el-tag v-for="p in detail.project.bidParticipants" :key="p" type="info" style="margin-right: 8px">
            {{ p }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <!-- 产品实体抽取结果 -->
    <div class="page-card section">
      <div class="page-title">产品实体抽取结果</div>
      <el-table :data="detail.products" stripe border>
        <el-table-column type="index" label="序号" width="60" align="center" />
        <el-table-column prop="productServiceName" label="产品/服务名称" min-width="160" />
        <el-table-column prop="category" label="品目" min-width="130" />
        <el-table-column prop="brand" label="品牌" width="100" />
        <el-table-column prop="specModel" label="规格型号" min-width="130" />
        <el-table-column label="单价" width="110" align="right">
          <template #default="{ row }">{{ formatMoney(row.unitPrice) }}</template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量" width="80" align="center" />
        <el-table-column label="总价" width="120" align="right">
          <template #default="{ row }">{{ formatMoney(row.totalPrice) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 原始文本（预留：之后实现原文与抽取结果对照） -->
    <div class="page-card section">
      <div class="page-title">原始文本</div>
      <pre class="raw-text">{{ detail.rawText }}</pre>
    </div>
  </div>

  <el-empty v-else description="未找到该公告的抽取结果" />
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { getExtractionDetail } from '../mock/mockExtractions'

const route = useRoute()

// 当前从 mock 中按公告编号取详情；接入后端后改为调用 getExtractionDetail(announcementId)
const detail = computed(() => getExtractionDetail(route.params.id))

function formatMoney(val) {
  if (val == null) return '-'
  return val.toLocaleString('zh-CN') + ' 元'
}

function formatAmount(val) {
  if (val == null) return '-'
  return val >= 10000 ? `${(val / 10000).toFixed(1)} 万元` : `${val} 元`
}
</script>

<style scoped>
.section {
  margin-bottom: 16px;
}

.raw-text {
  background: #f8f9fb;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px;
  font-size: 13px;
  line-height: 1.8;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
