import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const serverPort = process.env.SENSE_SERVER_PORT ?? '8787';

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.SENSE_WEB_PORT ?? 5173),
    strictPort: false,
    // The local SENSE server holds the broker; the web app talks to it same-origin through this proxy.
    proxy: {
      '/api': `http://127.0.0.1:${serverPort}`,
      '/ws': { target: `ws://127.0.0.1:${serverPort}`, ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
