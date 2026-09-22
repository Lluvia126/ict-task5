<template>
  <div class="page-card">
    <div class="page-title">抽取结果</div>
    <ExtractionTable :list="list" />
  </div>
</template>

<script setup>
import ExtractionTable from '../components/ExtractionTable.vue'
import { mockExtractionList } from '../mock/mockExtractions'

// 当前使用 mock 数据；接入后端后改为：
// import { getExtractions } from '../api/extraction'
// const list = ref([]); onMounted(async () => { list.value = await getExtractions() })
const list = mockExtractionList
</script>
