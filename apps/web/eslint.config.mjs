import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  { ignores: ["next-env.d.ts", "lib/api-schema.d.ts", ".next/**", "playwright-report/**", "test-results/**", "node_modules/**"] },
];
export default config;
