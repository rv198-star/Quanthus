import { defineConfig } from 'vite'
import vue2 from '@vitejs/plugin-vue2'

export default defineConfig({
  base: '/product-assets/',
  plugins: [vue2()],
  build: {
    target: 'es2020',
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false
  },
  server: {
    port: 3200,
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/data': 'http://127.0.0.1:3100'
    }
  }
})
