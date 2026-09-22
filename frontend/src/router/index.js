import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '../layouts/MainLayout.vue'

const routes = [
  {
    path: '/',
    component: MainLayout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('../views/Dashboard.vue'),
        meta: { title: '工作台' }
      },
      {
        path: 'upload',
        name: 'Upload',
        component: () => import('../views/Upload.vue'),
        meta: { title: '数据抽取' }
      },
      {
        path: 'extractions',
        name: 'ExtractionList',
        component: () => import('../views/ExtractionList.vue'),
        meta: { title: '抽取结果' }
      },
      {
        path: 'extractions/:id',
        name: 'ExtractionDetail',
        component: () => import('../views/ExtractionDetail.vue'),
        meta: { title: '抽取详情' }
      },
      {
        path: 'relations',
        name: 'RelationAnalysis',
        component: () => import('../views/RelationAnalysis.vue'),
        meta: { title: '关系分析' }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
