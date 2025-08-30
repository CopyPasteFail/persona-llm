// web/tailwind.config.ts
import type { Config } from "tailwindcss";

export default {
  content: [
    "./pages/**/*.{ts,tsx}",       // Next.js pages
    "./components/**/*.{ts,tsx}",  // components
    "./app/**/*.{ts,tsx}",         // only if you ever add the /app router
  ],
  theme: { extend: {} },
  plugins: [],
} satisfies Config;
