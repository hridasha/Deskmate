import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Loading data on mount is the normal pattern here; the rule is advisory.
      "react-hooks/set-state-in-effect": "warn",
      "@typescript-eslint/no-unused-vars": ["warn", { varsIgnorePattern: "^_", ignoreRestSiblings: true }],
    },
  },
  {
    // react-three-fiber works by mutating refs and shared objects every frame inside useFrame, which these React-compiler rules forbid.
    files: ["src/components/studio/**"],
    rules: { "react-hooks/immutability": "off", "react-hooks/refs": "off", "react-hooks/exhaustive-deps": "warn" },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
