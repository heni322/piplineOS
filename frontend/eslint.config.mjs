import { FlatCompat } from "@eslint/eslintrc";
import jsxA11y from "eslint-plugin-jsx-a11y";

const compat = new FlatCompat({ baseDirectory: import.meta.dirname });

const config = [
  { ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    plugins: { "jsx-a11y": jsxA11y },
    rules: {
      ...jsxA11y.configs.strict.rules,
      // Replies and scraped content are untrusted: never inject raw HTML.
      "react/no-danger": "error",
      // A table that scrolls sideways must be reachable by keyboard (WCAG 2.1.1, axe scrollable-region-focusable):
      // a focusable, labelled role="region" is the accepted pattern.
      "jsx-a11y/no-noninteractive-tabindex": ["error", { roles: ["tabpanel", "region"], tags: [] }],
    },
  },
];

export default config;
