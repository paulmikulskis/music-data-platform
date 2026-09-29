import { defineConfig, globalIgnores } from 'eslint/config';
import next from 'eslint-config-next/core-web-vitals';
import ts from 'eslint-config-next/typescript';
// Console links need full browser navigation. Authenticated artwork uses native images.
export default defineConfig([...next, ...ts, { rules: { '@next/next/no-html-link-for-pages': 'off', '@next/next/no-img-element': 'off' } }, globalIgnores(['.next/**', 'next-env.d.ts'])]);
