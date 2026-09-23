import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendTarget = process.env.MEETING_ASSISTANT_BACKEND_URL ?? 'http://127.0.0.1:8766';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5179,
    strictPort: true,
    proxy: { '/api': backendTarget },
  },
});
