# Vendored third-party libraries

These are committed, minified release builds — the live dashboard loads them
from its own origin so the page never touches the network at runtime (the same
offline guarantee the static dashboard has). Do not edit them except for the one
documented patch below.

| File | Library | Version | Source | License |
|------|---------|---------|--------|---------|
| `d3.v7.min.js` | D3 | 7.9.0 | https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js | ISC |
| `three.module.min.js` | three.js | 0.160.0 | https://unpkg.com/three@0.160.0/build/three.module.min.js | MIT |
| `OrbitControls.js` | three.js examples | 0.160.0 | https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js | MIT |
| `fonts/zilla-slab-{500,600,700}.woff2` | Zilla Slab | v12 | Google Fonts (`fonts.gstatic.com`) | OFL 1.1 |
| `fonts/hanken-grotesk-{400,500,600}.woff2` | Hanken Grotesk | v9 | Google Fonts (`fonts.gstatic.com`) | OFL 1.1 |
| `fonts/jetbrains-mono-{400,500}.woff2` | JetBrains Mono | v20 | Google Fonts (`fonts.gstatic.com`) | OFL 1.1 |

**Patch:** `OrbitControls.js` line 12 — its bare import `from 'three'` is rewritten
to `from './three.module.min.js'` so it resolves without an import map.

**Fonts:** only the **latin** subset of each weight is vendored (`fonts/*.woff2`, ~213 KB
total), and `fonts/fonts.css` declares the `@font-face` rules pointing at
`/assets/vendor/fonts/…` — self-hosted, no CDN, consistent with the CSP. The full
OFL 1.1 text ships with each family upstream; the standard permission grant applies
(use/study/modify/redistribute, including bundled in software, with the reserved
font names).

## Licenses

### D3 — ISC

> Copyright 2010–2023 Mike Bostock
>
> Permission to use, copy, modify, and/or distribute this software for any
> purpose with or without fee is hereby granted, provided that the above
> copyright notice and this permission notice appear in all copies.
>
> THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
> REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
> FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
> INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
> LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
> OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
> PERFORMANCE OF THIS SOFTWARE.

### three.js — MIT

> Copyright © 2010–2024 three.js authors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.
