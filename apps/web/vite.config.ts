import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { port: Number(process.env.SENSE_WEB_PORT ?? 5173), strictPort: false },
  build: { outDir: 'dist', sourcemap: false },
});
