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
