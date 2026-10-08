/** Colors are CSS variables so light/dark switch without touching components. */
const v = (n) => `rgb(var(--${n}) / <alpha-value>)`;
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: v("bg"), surface: v("surface"), sunken: v("sunken"), ink: v("ink"), muted: v("muted"), line: v("line"),
        accent: v("accent"), "accent-strong": v("accent-strong"), "accent-soft": v("accent-soft"), "on-accent": v("on-accent"),
        blue: v("blue"), ok: v("ok"), warn: v("warn"), danger: v("danger"),
        "ok-soft": v("ok-soft"), "warn-soft": v("warn-soft"), "danger-soft": v("danger-soft"), "blue-soft": v("blue-soft"),
      },
      fontFamily: {
        sans: ['"Atkinson Hyperlegible Next"', '"Noto Sans Bengali"', "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", '"Noto Sans Bengali"', "monospace"],
      },
      borderRadius: { ctl: "6px", panel: "10px" },
    },
  },
  plugins: [],
};
