import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

/**
 * Dev server + preview server.
 *
 * Both serve the SPA and proxy `/api` to the backend, so the browser only ever
 * talks to ONE origin: no CORS, no cross-origin preflight, no hardcoded
 * `localhost` that would mean the user's own machine when the page is opened
 * from anywhere else. `scripts/dev.mjs` sets `HG_BACKEND_ORIGIN` to the port
 * the backend actually landed on.
 *
 * Environment (all optional):
 *   HG_BACKEND_ORIGIN   proxy target          (default http://127.0.0.1:8000)
 *   VITE_DEV_HOST       bind address          (default 127.0.0.1 — use
 *                       0.0.0.0 to open the app from another device/browser
 *                       on your network; accounts work, but Secure cookies
 *                       are not set over plain HTTP on a LAN address)
 *   VITE_DEV_PORT       dev port              (default 5173)
 *   VITE_PREVIEW_PORT   preview port          (default 4173)
 *   VITE_ALLOWED_HOSTS  extra Host values the dev server accepts, comma
 *                       separated, or `*` to accept any (tunnels, machine
 *                       names). Vite otherwise rejects unknown hosts with
 *                       403 "Blocked request. This host is not allowed."
 */
export default defineConfig(({ mode }) => {
  const fileEnv = loadEnv(mode, process.cwd(), '');
  const read = (name: string): string =>
    ((process.env[name] ?? fileEnv[name] ?? '') as string).trim();

  const backendOrigin = read('HG_BACKEND_ORIGIN') || 'http://127.0.0.1:8000';
  const host = read('VITE_DEV_HOST') || '127.0.0.1';
  const devPort = Number(read('VITE_DEV_PORT') || '5173');
  const previewPort = Number(read('VITE_PREVIEW_PORT') || '4173');

  const allowedHostsRaw = read('VITE_ALLOWED_HOSTS');
  const allowedHosts =
    allowedHostsRaw === '*' || allowedHostsRaw.toLowerCase() === 'all'
      ? true
      : allowedHostsRaw
          .split(',')
          .map((entry) => entry.trim())
          .filter(Boolean);

  const proxy = {
    '/api': {
      target: backendOrigin,
      // Keep the browser's `Host`: the backend's CSRF check compares the
      // request's authority with the `Origin` header, so rewriting Host to the
      // backend's own port would make every cookie-authenticated write look
      // cross-origin (403). A deployment's reverse proxy should preserve Host
      // for the same reason, or declare its public address in APP_ORIGIN.
      changeOrigin: false,
    },
  };

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host,
      port: devPort,
      proxy,
      ...(allowedHosts === true || allowedHosts.length > 0 ? { allowedHosts } : {}),
    },
    preview: {
      host,
      port: previewPort,
      proxy,
      ...(allowedHosts === true || allowedHosts.length > 0 ? { allowedHosts } : {}),
    },
  };
});
