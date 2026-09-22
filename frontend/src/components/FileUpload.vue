<template>
  <div>
    <el-upload
      class="upload-area"
      drag
      action="#"
      :auto-upload="false"
      :on-change="handleFileChange"
      :show-file-list="false"
      accept=".html,.pdf,.doc,.docx,.xlsx,.zip"
    >
      <el-icon class="upload-icon" :size="48"><UploadFilled /></el-icon>
      <div class="upload-text">将文件拖到此处，或点击选择文件</div>
      <div class="upload-tip">支持 HTML / PDF / DOC / DOCX / XLSX / ZIP 格式</div>
    </el-upload>

    <div v-if="file" class="file-info">
      <el-icon><Document /></el-icon>
      <span class="file-name">{{ file.name }}</span>
      <span class="file-size">{{ (file.size / 1024).toFixed(1) }} KB</span>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled, Document } from '@element-plus/icons-vue'

const ALLOWED_TYPES = ['html', 'pdf', 'doc', 'docx', 'xlsx', 'zip']

const emit = defineEmits(['file-selected'])

const file = ref(null)

function handleFileChange(uploadFile) {
  const raw = uploadFile.raw
  const ext = raw.name.split('.').pop().toLowerCase()
  if (!ALLOWED_TYPES.includes(ext)) {
    ElMessage.warning('不支持的文件类型，请上传 HTML / PDF / DOC / DOCX / XLSX / ZIP 文件')
    return
  }
  file.value = raw
  emit('file-selected', raw)
}

// 供父页面清空已选文件
function clearFile() {
  file.value = null
}

defineExpose({ clearFile })
</script>

<style scoped>
.upload-area :deep(.el-upload-dragger) {
  padding: 40px 0;
}

.upload-icon {
  color: #409eff;
  margin-bottom: 8px;
}

.upload-text {
  font-size: 15px;
  color: #303133;
}

.upload-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 6px;
}

.file-info {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #409eff;
  font-size: 14px;
}

.file-size {
  color: #909399;
}
</style>
