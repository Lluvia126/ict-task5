import axios from 'axios'

// 统一 API 基础地址，来自环境变量 VITE_API_BASE_URL
// 在 frontend/.env.development 中配置，例如 http://localhost:8000/api
const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 60000
})

// 请求拦截器（预留：可在这里加 token 等）
request.interceptors.request.use(config => config)

// 响应拦截器（预留：统一错误处理）
request.interceptors.response.use(
  res => res.data,
  err => Promise.reject(err)
)

export default request
