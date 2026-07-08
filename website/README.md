# Company Brain — documentation site

The public docs for the `brain` CLI and the multi-tenant company brain, built with
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

## Structure

```
docs.json                     navigation + theme
index.mdx                     overview
getting-started.mdx           operator quickstart
guides/                       per-employee-setup, getting-things-in, deployment
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
