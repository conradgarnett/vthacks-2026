import basicSsl from "@vitejs/plugin-basic-ssl";
import { defineConfig } from "vite";

// HTTPS is not optional: getUserMedia refuses to hand over the camera on a
// non-secure origin, and a phone reaching this over LAN is not localhost.
// basicSsl generates a self-signed cert so this works with no tunnel and no
// network access -- which matters when the venue WiFi is the thing failing.
export default defineConfig({
  plugins: [basicSsl()],
  server: {
    host: true, // bind 0.0.0.0 so the phone can reach it
    port: 5173,
    // Everything backend is proxied through this origin on purpose.
    //
    // The page is served over HTTPS, so the browser will only open a wss://
    // socket -- but running TLS on the backend too would mean the phone has to
    // accept a SECOND self-signed cert, and Safari never prompts for one on a
    // WebSocket. It just fails silently and reconnects forever.
    //
    // Proxying keeps the client same-origin: one cert, already accepted, and
    // Vite terminates TLS before talking plain HTTP to uvicorn.
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/health": { target: "http://127.0.0.1:8000" },
      "/scene": { target: "http://127.0.0.1:8000" },
      "/metrics": { target: "http://127.0.0.1:8000" },
    },
  },
  build: {
    target: "es2022",
    rollupOptions: {
      input: {
        main: "index.html",
        judge: "judge/index.html",
      },
    },
  },
});
