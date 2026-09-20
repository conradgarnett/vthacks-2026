import basicSsl from "@vitejs/plugin-basic-ssl";
import { defineConfig } from "vite";

// HTTPS is not optional for a phone: getUserMedia refuses to hand over the
// camera on a non-secure origin, and a phone reaching this over LAN is not
// localhost. basicSsl generates a self-signed cert so this works with no
// tunnel and no network access -- which matters when the venue WiFi is the
// thing failing.
//
// VISIONOS_HTTP=1 serves plain HTTP instead, for a browser on this machine:
// localhost is a secure context already, and some embedded browsers refuse
// a self-signed certificate outright.
const plainHttp = process.env.VISIONOS_HTTP === "1";

export default defineConfig({
  plugins: plainHttp ? [] : [basicSsl()],
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
      // The place memory, read and edited by the panel.
      "/places": { target: "http://127.0.0.1:8000" },
      "/scenes": { target: "http://127.0.0.1:8000" },
      // The wearer's profile and the mail check, for the allergies sheet.
      "/profile": { target: "http://127.0.0.1:8000" },
      "/alerts": { target: "http://127.0.0.1:8000" },
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
