# brainkit — documentation site

The public docs for brainkit and its `brain` CLI — the tool for running a
multi-tenant company brain — built with
[Holocron](https://holocron.so) (an MIT, self-hostable Mintlify replacement that
runs as a Vite plugin). Pages are MDX; navigation lives in `docs.json`.

## Develop

```bash
npm install
npm run dev        # http://localhost:5173 with hot reload
```

## Build and serve

```bash
npm run build      # → dist/
npm start          # node dist/rsc/index.js
```

## Deploy

This is a server-rendered app, not a static site. `npm run build` prerenders
**zero** routes by design, so a static host (GitHub Pages, plain S3) cannot
serve it — deployment needs a Node runtime.

Two paths work:

1. **`holocron deploy`** — Holocron's own hosting, the path the framework
   vendor supports and tests. Authenticates with `HOLOCRON_KEY` in CI, and
   `holocron domain add` attaches a custom domain.
2. **Any Node host** (Fly, Render, Railway, or a systemd unit next to
   `brain-webhook.service`) running the standalone server:

   ```bash
   npm ci && npm run build
   npm start          # node dist/rsc/index.js, honors $PORT
   ```

### Vercel does not currently work

Vercel sets `VERCEL=1`, which auto-activates the spiceflow
[Build Output API](https://vercel.com/docs/build-output-api/v3) adapter. The
adapter runs and emits a valid-looking tree (`nodejs22.x` streaming function
plus CDN assets), the build succeeds — and then **every HTML route returns
500**. The SSR render itself completes: the 500 response body carries the full
RSC flight payload with `__NO_HYDRATE=1` set, so something after render sets
the status. The `.md` and `llms.txt` exports still serve 200, which is why a
bot fetching the site can make it look healthy.

The same SSR bundle serves every route 200 as a standalone server on both
Node 22 and Node 24+, so this is the adapter, not the build, the content, or
the Node version. It's upstream in `spiceflow@1.26.0-rsc.7` — a pre-release.
Re-check when Holocron/spiceflow cut a stable RSC release; until then use one
of the two paths above.

To reproduce the Vercel artifact locally:

```bash
VERCEL=1 npm run build    # → .vercel/output/ (git-ignored)
```

## Structure

```
docs.json                     navigation + theme
index.mdx                     overview
getting-started.mdx           operator quickstart
guides/                       per-employee-setup, getting-things-in, deployment, reference-deployment
concepts/                     spaces-and-permissions, the-compiler, promotions, retrieval
reference/                    cli, configuration
```

## Agent-native exports

Holocron serves the docs in agent-consumable forms — fitting, since the product
itself is agent-native:

- `/llms.txt` — index for LLMs
- `/llms-full.txt` — the whole site in one file
- `/<page>.md` — the raw markdown for any page
- `/docs.zip` — every page as markdown, for local grep

## Requirements

Node 20+ and Vite 8 (pinned in `package.json`; Holocron 0.23+ requires Vite 8).

## Vulnerability posture

`npm audit` here does not report zero, on purpose.

Everything fixable without a downgrade is fixed — the tree carries no high or
critical advisories. What remains is three **moderate** advisories, all one
transitive chain:

```
@holocron.so/vite → @modelcontextprotocol/sdk → @hono/node-server
```

`npm audit fix --force` "resolves" them by installing `@holocron.so/vite@0.18.2`
— ten minor versions backwards, past the release that made the site deployable
at all. That is not a fix; it trades a working docs site for a lower number.

Taking it is also unnecessary, for two independent reasons:

- **The advisory is Windows-only.** `serve-static` mis-resolves an encoded
  backslash (`%5C`) because the *Windows* path resolver treats `\` as a
  separator. Production runs `nodejs22.x` on Linux, where `%5C` is an ordinary
  character in a filename. Directory escape via `..` was never affected.
- **The package isn't in the deployed function.** spiceflow traces the function's
  dependencies with `@vercel/nft`; `@hono/node-server` is not among them and is
  not bundled — nothing in `dist/` requires it. It is reached only by the SDK's
  own server, which this site never starts.

Re-check when Holocron picks up a patched `@modelcontextprotocol/sdk`; the right
resolution is upstream moving forward, not this repo moving back. Until then,
run `npm audit` and expect exactly these three.
