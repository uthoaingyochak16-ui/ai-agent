/** @type {import('tailwindcss').Config} */
const c = (v) => `rgb(var(--${v}) / <alpha-value>)`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: c("bg"), surface: c("surface"), sunken: c("sunken"), ink: c("ink"), muted: c("muted"), line: c("line"),
        accent: { DEFAULT: c("accent"), strong: c("accent-strong"), soft: c("accent-soft") },
        "on-accent": c("on-accent"),
        brass: { DEFAULT: c("brass"), soft: c("brass-soft") },
        side: { DEFAULT: c("side"), raised: c("side-raised"), ink: c("side-ink"), muted: c("side-muted"), line: c("side-line") },
        blue: { DEFAULT: c("blue"), soft: c("blue-soft") },
        ok: { DEFAULT: c("ok"), soft: c("ok-soft") },
        warn: { DEFAULT: c("warn"), soft: c("warn-soft") },
        danger: { DEFAULT: c("danger"), soft: c("danger-soft") },
      },
      fontFamily: {
        sans: ['"Instrument Sans"', '"Noto Sans Bengali"', "system-ui", "sans-serif"],
        display: ['"Bricolage Grotesque Variable"', '"Instrument Sans"', '"Noto Sans Bengali"', "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { ctl: "10px", panel: "16px" },
      boxShadow: {
        sm: "0 1px 2px rgb(var(--shadow) / 0.06)",
        panel: "0 1px 2px rgb(var(--shadow) / 0.04), 0 8px 24px -12px rgb(var(--shadow) / 0.10)",
        lg: "0 10px 30px -8px rgb(var(--shadow) / 0.22)",
        xl: "0 24px 60px -16px rgb(var(--shadow) / 0.35)",
      },
    },
  },
  plugins: [],
};
