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

Three paths work:

1. **Vercel** — what this site runs on today
   ([brainkit-docs.vercel.app](https://brainkit-docs.vercel.app)). Vercel sets
   `VERCEL=1`, which auto-activates the spiceflow
   [Build Output API](https://vercel.com/docs/build-output-api/v3) adapter; it
   emits a `nodejs22.x` streaming function plus CDN assets. No `vercel.json` is
   needed. Reproduce the artifact locally with:

   ```bash
   VERCEL=1 npm run build    # → .vercel/output/ (git-ignored)
   ```

2. **`holocron deploy`** — Holocron's own hosting, the path the framework
   vendor supports and tests. Authenticates with `HOLOCRON_KEY` in CI, and
   `holocron domain add` attaches a custom domain.
3. **Any Node host** (Fly, Render, Railway, or a systemd unit next to
   `brain-webhook.service`) running the standalone server:

   ```bash
   npm ci && npm run build
   npm start          # node dist/rsc/index.js, honors $PORT
   ```

### Two things keep Vercel working

Both are load-bearing, and both look like inert config until they're removed.

**`ssr.noExternal: ['scheduler']` in `vite.config.ts`.** spiceflow traces the
function's dependencies with `@vercel/nft` starting from the RSC entry, and that
trace misses `scheduler`: it is reachable only from `dist/ssr/index.js`, so
`react` and `react-dom` get copied into the function and their own dependency
does not. On a Node host nothing breaks — the full `node_modules` is right
there — but the Vercel function ships only the traced set, and every HTML route
dies with `MODULE_NOT_FOUND: Cannot find module 'scheduler'`. Inlining sidesteps
the trace entirely. Drop it once the tracer follows transitive deps into the SSR
chunk.

**`overrides.spiceflow` in `package.json`.** `@holocron.so/vite` pins spiceflow
exactly, and its own dependency `@holocron.so/cli` pins an *older* exact
version. npm honors both and installs two copies, which spiceflow itself refuses
to build with (`Duplicate spiceflow installation detected`). Two exact pins
cannot be reconciled by `npm dedupe`, so one winner is pinned here. **Keep it in
lockstep with the spiceflow version in `@holocron.so/vite`'s dependencies on
every Holocron upgrade** — `.github/dependabot.yml` carries the same warning for
whoever reviews the bot's PR.

A failure mode worth knowing when debugging this site: Holocron content-negotiates,
serving `.md` and `llms.txt` to bots and agents. Those kept returning 200 while
every HTML route was returning 500, so a plain `curl` or a link checker reported
the site healthy when browsers saw nothing. Check with a browser User-Agent.

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
