<template>
  <div>
    <div class="page-card">
      <div class="page-title">数据抽取</div>
      <p class="page-desc">上传招投标公告文件，系统将自动完成文件解析与 AI 实体抽取。</p>

      <!-- 步骤条：上传文件 -> 文件解析 -> AI实体抽取 -> 抽取完成 -->
      <el-steps :active="stepIndex" align-center class="steps">
        <el-step title="上传文件" />
        <el-step title="文件解析" />
        <el-step title="AI实体抽取" />
        <el-step title="抽取完成" />
      </el-steps>

      <FileUpload v-show="stepIndex === 0" ref="uploadRef" @file-selected="onFileSelected" />

      <!-- 处理中提示 -->
      <div v-if="stepIndex === 1 || stepIndex === 2" class="processing">
        <el-icon class="is-loading" :size="32" color="#409eff"><Loading /></el-icon>
        <p>{{ processingText }}</p>
        <el-progress :percentage="progress" :stroke-width="10" style="width: 320px" />
      </div>

      <!-- 完成结果 -->
      <div v-if="stepIndex === 3" class="result">
        <el-result icon="success" title="抽取完成" sub-title="AI 已从公告中抽取结构化实体">
          <template #extra>
            <el-descriptions :column="2" border style="text-align: left">
              <el-descriptions-item label="公告编号">{{ mockResult.announcementId }}</el-descriptions-item>
              <el-descriptions-item label="项目名称">{{ mockResult.projectName }}</el-descriptions-item>
              <el-descriptions-item label="采购单位">{{ mockResult.purchaser }}</el-descriptions-item>
              <el-descriptions-item label="中标供应商">{{ mockResult.winningSupplier }}</el-descriptions-item>
            </el-descriptions>
            <div style="margin-top: 16px">
              <el-button type="primary" @click="$router.push('/extractions')">查看抽取结果</el-button>
              <el-button @click="reset">继续上传</el-button>
            </div>
          </template>
        </el-result>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import FileUpload from '../components/FileUpload.vue'
import { mockExtractionList } from '../mock/mockExtractions'

// 当前步骤：0 上传 / 1 解析 / 2 抽取 / 3 完成
const stepIndex = ref(0)
const progress = ref(0)
const uploadRef = ref(null)

const processingText = computed(() =>
  stepIndex.value === 1 ? '正在解析文件内容...' : 'AI 正在抽取实体...'
)

// 演示用的抽取结果（取 mock 列表第一条）
const mockResult = mockExtractionList[0]

function onFileSelected(file) {
  // 当前使用 mock 模拟流程：上传 -> 解析 -> 抽取 -> 完成
  // 接入后端后改为：await uploadFile() -> await parseFile() -> await extractFile()
  simulateProcessing()
}

function simulateProcessing() {
  stepIndex.value = 1
  progress.value = 0
  const timer = setInterval(() => {
    progress.value += 5
    if (progress.value >= 50) {
      // 解析完成，进入 AI 抽取
      stepIndex.value = 2
    }
    if (progress.value >= 100) {
      clearInterval(timer)
      stepIndex.value = 3
    }
  }, 120)
}

function reset() {
  stepIndex.value = 0
  progress.value = 0
  uploadRef.value?.clearFile()
}
</script>

<style scoped>
.page-desc {
  font-size: 13px;
  color: #909399;
  margin-bottom: 20px;
}

.steps {
  margin: 10px 0 30px;
}

.processing {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 30px 0;
  color: #606266;
}

.result {
  text-align: center;
}
</style>
