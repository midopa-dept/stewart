// stewart 허브 화면 빌드: 결과는 ../src/stewart/web_ui/ 에 고정 이름(index.html·app.js·app.css)으로 — 커밋해서 받는 쪽은 Python 만.
import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: '/ui/',
  plugins: [preact(), tailwindcss()],
  build: {
    outDir: '../src/stewart/web_ui',
    emptyOutDir: false,   // 이름이 고정(index.html·app.js·app.css)이라 덮어쓴다. 폴더를 비우지 않는다(앱이 만든 .claude 등을 건드리지 않게)
    target: 'es2020',
    modulePreload: false,
    cssCodeSplit: false,
    reportCompressedSize: true,
    rollupOptions: {
      output: {
        entryFileNames: 'app.js',
        chunkFileNames: 'app-[name].js',
        assetFileNames: 'app.[ext]',
        codeSplitting: false,
      },
    },
  },
})
