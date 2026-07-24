import { defineConfig } from 'vite'
import { holocron } from '@holocron.so/vite'

export default defineConfig({
  plugins: [holocron()],

  // Bundle `scheduler` into the SSR output instead of leaving it as a runtime
  // `require("scheduler")`.
  //
  // spiceflow traces the function's dependencies with @vercel/nft starting from
  // the RSC entry. That trace misses this one: `scheduler` is reached only from
  // dist/ssr/index.js, so react and react-dom get copied into the serverless
  // bundle and their own dependency does not. On a normal Node deploy nothing
  // breaks — the full node_modules is right there — but on Vercel the function
  // ships only the traced set, and every HTML route dies with
  // `MODULE_NOT_FOUND: Cannot find module 'scheduler'`.
  //
  // Inlining sidesteps the trace entirely: no require, nothing to miss. Drop
  // this once the tracer follows transitive deps into the SSR chunk.
  ssr: {
    noExternal: ['scheduler'],
  },
})
