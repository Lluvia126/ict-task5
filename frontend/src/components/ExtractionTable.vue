<template>
  <el-table :data="list" stripe>
    <el-table-column prop="announcementId" label="公告编号" width="130" />
    <el-table-column prop="projectName" label="项目名称" min-width="220" show-overflow-tooltip />
    <el-table-column prop="purchaser" label="采购单位" min-width="160" show-overflow-tooltip />
    <el-table-column prop="winningSupplier" label="中标供应商" min-width="180" show-overflow-tooltip />
    <el-table-column label="项目金额" width="120">
      <template #default="{ row }">
        {{ formatAmount(row.projectAmount) }}
      </template>
    </el-table-column>
    <el-table-column prop="productCount" label="产品数量" width="90" align="center" />
    <el-table-column label="抽取状态" width="100" align="center">
      <template #default="{ row }">
        <StatusTag :status="row.status" />
      </template>
    </el-table-column>
    <el-table-column label="操作" width="100" fixed="right">
      <template #default="{ row }">
        <el-button type="primary" link @click="$router.push(`/extractions/${row.announcementId}`)">
          查看详情
        </el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup>
import StatusTag from './StatusTag.vue'

defineProps({
  list: { type: Array, required: true }
})

function formatAmount(val) {
  if (val == null) return '-'
  return val >= 10000 ? `${(val / 10000).toFixed(1)} 万` : `${val} 元`
}
</script>
