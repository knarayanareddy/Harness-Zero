/**
 * Tailwind build for the shipped web client (harness/frontends/web/static).
 *
 * The UI has exactly two stylesheets, both served from /static under the strict CSP:
 *   styles.css        hand-written components and layout (semantic classes);
 *   vendor/tw.css     the utilities the templates actually use, compiled from this config.
 *
 * `vendor/tw.css` is a committed build artifact: `npm run build:css` regenerates it from
 * index.html + *.js, and CI fails when the committed file is stale (see .github/workflows/ci.yml).
 * Preflight is off so the compiled file never fights styles.css's own reset.
 */
module.exports = {
  content: [
    "./harness/frontends/web/static/index.html",
    "./harness/frontends/web/static/*.js",
  ],
  corePlugins: { preflight: false },
  theme: {
    // The client is a dense, IDE-like layout: keep the compact type scale styles.css was written for.
    fontSize: {
      xs: ["11px", "16px"],
      sm: ["13px", "18px"],
      base: ["14px", "20px"],
      lg: ["16px", "24px"],
    },
    fontFamily: {
      sans: ["var(--font-sans)"],
      mono: ["var(--font-mono)"],
    },
    extend: {},
  },
  plugins: [],
};
