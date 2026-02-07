import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        warp: {
          bg: "#0a0a0a",
          surface: "#141414",
          border: "#262626",
          fg: "#fafafa",
          muted: "#a3a3a3",
          accent: "#38bdf8",
          green: "#4ade80",
          red: "#f87171",
          yellow: "#fbbf24",
        },
      },
      fontFamily: {
        mono: ["var(--font-jetbrains)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
