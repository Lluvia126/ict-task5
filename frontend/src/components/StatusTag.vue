<template>
  <el-tag :type="tagType" size="small">{{ text }}</el-tag>
</template>

<script setup>
import { computed } from 'vue'

// 统一管理状态 -> 标签的映射
const STATUS_MAP = {
  pending: { type: 'info', text: '待处理' },
  parsing: { type: 'warning', text: '解析中' },
  extracting: { type: 'warning', text: 'AI抽取中' },
  done: { type: 'success', text: '已完成' },
  failed: { type: 'danger', text: '失败' }
}

const props = defineProps({
  status: { type: String, required: true }
})

const tagType = computed(() => STATUS_MAP[props.status]?.type || 'info')
const text = computed(() => STATUS_MAP[props.status]?.text || props.status)
</script>
