import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // iOS 自动化 AI 推理较慢，必须设置长超时
    testTimeout: 120_000,
    hookTimeout: 60_000,
    globals: true,
    sequence: {
      // iOS 同一设备不支持并行，强制串行
      concurrent: false,
    },
  },
});
