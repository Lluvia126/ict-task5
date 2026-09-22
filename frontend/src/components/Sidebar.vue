<template>
  <el-aside width="220px" class="sidebar">
    <div class="logo">
      <el-icon :size="22" color="#409eff"><DataAnalysis /></el-icon>
      <span class="logo-text">智能实体挖掘系统</span>
    </div>
    <el-menu
      :default-active="route.path"
      router
      background-color="#001529"
      text-color="#a6adb4"
      active-text-color="#ffffff"
      class="menu"
    >
      <el-menu-item index="/dashboard">
        <el-icon><Odometer /></el-icon>
        <span>工作台</span>
      </el-menu-item>
      <el-menu-item index="/upload">
        <el-icon><Upload /></el-icon>
        <span>数据抽取</span>
      </el-menu-item>
      <el-menu-item index="/extractions">
        <el-icon><Document /></el-icon>
        <span>抽取结果</span>
      </el-menu-item>
      <el-menu-item index="/relations">
        <el-icon><Share /></el-icon>
        <span>关系分析</span>
      </el-menu-item>
    </el-menu>
    <!-- 底部版本标识，保持紧凑，不占大面积空白 -->
    <div class="sidebar-footer">
      <el-icon color="#4a5560"><Cpu /></el-icon>
      <span>v0.1.0 · baseline</span>
    </div>
  </el-aside>
</template>

<script setup>
import { useRoute } from 'vue-router'
import {
  DataAnalysis, Odometer, Upload, Document, Share, Cpu
} from '@element-plus/icons-vue'

const route = useRoute()
</script>

<style scoped>
.sidebar {
  background-color: #001529;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #fff;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.logo-text {
  font-size: 15px;
  font-weight: 600;
}

.menu {
  border-right: none;
  flex: 1;
}

:deep(.el-menu-item) {
  height: 48px;
  line-height: 48px;
}

:deep(.el-menu-item.is-active) {
  background-color: #409eff;
}

.sidebar-footer {
  padding: 12px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 12px;
  color: #4a5560;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
</style>
