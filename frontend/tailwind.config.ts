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
          bg: "#181818",
          surface: "#1e1e1e",
          border: "#2d2d2d",
          fg: "#d8d8d8",
          muted: "#858585",
          accent: "#7cafc2",
          green: "#a1b56c",
          red: "#ab4642",
          yellow: "#f7ca88",
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
