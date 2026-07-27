import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // loadEnv reads .env/.env.local/etc. from this package's root — the same
  // files saviour-config's build_frontend() writes VITE_VARIANT into.
  const env = loadEnv(mode, process.cwd(), '')
  const variant = env.VITE_VARIANT || 'basic'

  return {
    plugins: [react()],
    resolve: {
      alias: {
        // main.jsx imports this fixed specifier rather than a hardcoded
        // './<variant>/App' path. Resolving it via alias (evaluated here in
        // Node, at config time) means only the one selected variant's App.jsx
        // — and whatever it actually imports — is ever read or transformed;
        // the other four variants' folders are untouched by the build, same
        // as the old single-hardcoded-import approach. A broken/unmaintained
        // variant can't break a build that doesn't select it.
        'virtual:active-app': path.resolve(__dirname, `src/${variant}/App.jsx`),
      },
    },
  }
})
