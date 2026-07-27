/**
 * Vite 构建配置
 *
 * 配置：
 * - 开发服务器端口和代理（后端 API 代理）
 * - React 插件
 * - 路径别名 (@/ → src/)
 */

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  base: './',

  // 路径别名
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },

  // 开发服务器配置
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // 后端 API 代理到 FastAPI 服务
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },

  // 构建配置
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
