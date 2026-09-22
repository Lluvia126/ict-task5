<template>
  <el-container class="layout">
    <Sidebar />
    <el-container class="main-container">
      <Header />
      <el-main class="layout-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import Sidebar from '../components/Sidebar.vue'
import Header from '../components/Header.vue'
</script>

<style scoped>
.layout {
  height: 100%;
}

.main-container {
  height: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.layout-main {
  padding: 20px;
  background-color: #f5f7fa;
  overflow-y: auto;
  overflow-x: hidden;
}
</style>
