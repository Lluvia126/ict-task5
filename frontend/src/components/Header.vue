<template>
  <el-header class="header">
    <div class="header-left">
      <span class="system-name">面向行业数据的智能实体挖掘与关系建模系统</span>
      <el-divider direction="vertical" />
      <span class="page-name">{{ route.meta.title }}</span>
    </div>
    <div class="header-right">
      <el-tag type="success" effect="dark" size="small">系统运行中</el-tag>
    </div>
  </el-header>
</template>

<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<style scoped>
.header {
  height: 56px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid #e4e7ed;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.06);
}

.header-left {
  display: flex;
  align-items: center;
}

.system-name {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.page-name {
  font-size: 14px;
  color: #909399;
}

.header-right {
  display: flex;
  align-items: center;
}
</style>
