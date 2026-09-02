import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// Dev-only middleware for mock mode (npm run dev:mock): the camera livestream
// <img> tags and the "take a picture" fetch point at http://<module.ip>:8080/…
// which doesn't exist without real modules. Fixtures set module.ip to "mock",
// and the stream/snapshot URL builders (src/basic/utils/streamUrls.js) then
// point here. We answer with an SVG placeholder that names the tile so the
// grid is still readable. (A snapshot saved from this is SVG-bytes-as-.jpg --
// fine for a mock; you're testing the button flow, not the file.)
function mockMediaPlugin() {
  return {
    name: 'saviour-mock-media',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/mock-media', (req, res) => {
        const url = new URL(req.url, 'http://localhost')
        const label = url.searchParams.get('label') || 'MOCK CAMERA'
        const t = new Date().toLocaleTimeString()
        res.setHeader('Content-Type', 'image/svg+xml')
        res.setHeader('Cache-Control', 'no-store')
        res.setHeader('Access-Control-Allow-Origin', '*')
        res.end(`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480">
  <rect width="100%" height="100%" fill="#1c2128"/>
  <rect x="8" y="8" width="624" height="464" fill="none" stroke="#3d444d" stroke-width="2"/>
  <text x="320" y="230" fill="#8b949e" font-family="system-ui,sans-serif"
        font-size="28" text-anchor="middle">${label}</text>
  <text x="320" y="266" fill="#6e7681" font-family="ui-monospace,monospace"
        font-size="18" text-anchor="middle">mock livestream · ${t}</text>
</svg>`)
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // loadEnv reads .env/.env.local/etc. from this package's root — the same
  // files saviour-config's build_frontend() writes VITE_VARIANT into.
  const env = loadEnv(mode, process.cwd(), '')
  const variant = env.VITE_VARIANT || 'basic'
  const mock = env.VITE_MOCK === '1'

  return {
    plugins: [react(), ...(mock ? [mockMediaPlugin()] : [])],
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
    server: {
      fs: {
        // Guide.jsx (src/docs/loadDocs.js) globs docs/readthedocs/ at the
        // repo root, outside this package — only affects `vite`'s dev-server
        // file-serving middleware; `vite build` reads files directly via
        // Node and isn't subject to this allowlist.
        allow: [path.resolve(__dirname, '../../..')],
      },
    },
  }
})
