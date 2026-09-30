// Trusted refactorX ESLint configuration (crp-eslint-v1).
// Platform-owned: uploaded projects cannot supply or modify it, and inline
// `eslint-disable` / config comments are ignored (allowInlineConfig: false).
// Every enabled rule has an entry in packages/analysis/.../rules/catalog.json.
import globals from "globals";
import tseslint from "typescript-eslint";

const coreRules = {
  "no-eval": "error",
  "no-implied-eval": "error",
  "no-new-func": "error",
  "no-script-url": "error",
  "eqeqeq": ["error", "always"],
  "no-debugger": "error",
  "no-dupe-keys": "error",
  "no-dupe-else-if": "error",
  "no-duplicate-case": "error",
  "no-unreachable": "error",
  "no-unsafe-finally": "error",
  "no-unsafe-negation": "error",
  "no-self-compare": "error",
  "no-self-assign": "error",
  "no-cond-assign": "error",
  "no-constant-condition": "error",
  "no-empty": "error",
  "no-fallthrough": "error",
  "no-prototype-builtins": "error",
  "no-sparse-arrays": "error",
  "no-loss-of-precision": "error",
  "no-async-promise-executor": "error",
  "no-compare-neg-zero": "error",
  "use-isnan": "error",
  "valid-typeof": "error",
  "no-var": "error",
  "prefer-const": "error",
};

const sharedGlobals = { ...globals.browser, ...globals.node };

export default [
  {
    files: ["**/*.{js,mjs,cjs,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: sharedGlobals,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    linterOptions: { reportUnusedDisableDirectives: "off" },
    rules: { ...coreRules, "no-unused-vars": "error" },
  },
  {
    // Lightning Web Components (Salesforce) use decorators (@api, @track, @wire), which the
    // default JavaScript parser rejects; the TypeScript parser reads them. Same rules as above.
    files: ["**/lwc/**/*.js"],
    languageOptions: {
      parser: tseslint.parser,
      ecmaVersion: "latest",
      sourceType: "module",
      globals: sharedGlobals,
    },
    linterOptions: { reportUnusedDisableDirectives: "off" },
    rules: { ...coreRules, "no-unused-vars": "error" },
  },
  {
    files: ["**/*.{ts,mts,cts,tsx}"],
    languageOptions: {
      parser: tseslint.parser,
      ecmaVersion: "latest",
      sourceType: "module",
      globals: sharedGlobals,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { "@typescript-eslint": tseslint.plugin },
    linterOptions: { reportUnusedDisableDirectives: "off" },
    rules: { ...coreRules, "@typescript-eslint/no-unused-vars": "error" },
  },
];
