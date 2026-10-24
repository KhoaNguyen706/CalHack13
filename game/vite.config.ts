import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

declare const process: { env: Record<string, string | undefined> };

// One address for every device: /api goes to the coordinator, /agents-api to the agents service and /stdb to
// SpacetimeDB, so the page works on localhost, a Wi-Fi IP or a public tunnel without baking addresses into the build.
const coordinator = process.env.COORDINATOR_PROXY ?? 'http://localhost:8000';
const spacetime = process.env.SPACETIME_PROXY ?? 'http://localhost:3000';
const agents = process.env.AGENTS_PROXY ?? 'http://localhost:8100';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: true, // tunnel hostnames (*.trycloudflare.com) change every run
    proxy: {
      '/api': { target: coordinator, changeOrigin: true, ws: true, rewrite: p => p.replace(/^\/api/, '') },
      '/agents-api': { target: agents, changeOrigin: true, rewrite: p => p.replace(/^\/agents-api/, '') },
      '/stdb': { target: spacetime, changeOrigin: true, ws: true, rewrite: p => p.replace(/^\/stdb/, '') },
    },
  },
  // Heroku serves the built app with `vite preview` on its own hostname.
  preview: { allowedHosts: true },
});
