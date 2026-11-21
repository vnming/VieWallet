import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["**/dist/**", "**/node_modules/**", "**/coverage/**", "**/schema.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
      // Money must never go through floating-point parsing in the UI (ADR-0003).
      "no-restricted-globals": [
        "error",
        { name: "parseFloat", message: "Do not parse money as float; use parseMinorUnits()." },
      ],
      "no-restricted-properties": [
        "error",
        { object: "Number", property: "parseFloat", message: "Use parseMinorUnits()." },
      ],
    },
  },
  {
    // Fast refresh boundaries matter in apps; shared packages export hooks next to components.
    files: ["apps/*/src/**/*.tsx"],
    ignores: ["**/*.test.tsx"],
    rules: { "react-refresh/only-export-components": ["error", { allowConstantExport: true }] },
  },
  {
    files: ["**/*.test.{ts,tsx}", "vitest.setup.ts"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/require-await": "off",
    },
  },
  {
    files: ["*.config.{js,ts}", "**/vite.config.ts", "vitest.setup.ts"],
    languageOptions: { globals: { ...globals.node } },
  },
  { files: ["**/*.js"], ...tseslint.configs.disableTypeChecked },
);
