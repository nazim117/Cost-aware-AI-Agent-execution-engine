import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8081',
        rewrite: path => path.replace(/^\/api/, ''),
      },
      '/agent': 'http://localhost:8081',
      '/metrics': 'http://localhost:8081',
      '/runs': 'http://localhost:8081',
    }
  }
})