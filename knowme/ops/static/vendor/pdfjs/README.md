# Vendored pdf.js

**Version: 6.3.289** · [pdfjs-dist on npm](https://www.npmjs.com/package/pdfjs-dist)
· Source: `https://cdn.jsdelivr.net/npm/pdfjs-dist@6.3.289/`
· License: **Apache-2.0** (see `LICENSE` in this directory).

This directory is checked in, not installed. KnowMe's frontend has no build
step and no dependencies, so a PDF renderer has to be a file we ship — the
browser loads it straight from `/static/vendor/pdfjs/`.

## Why this version, and why `.mjs`

pdf.js **3.11.174** is the last release with a classic UMD build (a global
`pdfjsLib`), which would have matched this codebase's classic-script model most
simply. It is not usable: versions before **4.2.67** are affected by
**CVE-2024-4367**, where a crafted `fontMatrix` in a PDF executes JavaScript in
the page. Older PDF-renderer builds are the last thing to pin.

So: version 6.x, which ships **ESM only**. `js/reader.js` loads it with a
dynamic `import()` — that is still zero-build (native browser modules, no
bundler) and keeps everything else in the classic-script global scope. The one
requirement it adds is server-side: `dashboard.py::_serve_static` must send
`.mjs` as `text/javascript`, because a browser refuses to evaluate a module
served as `application/octet-stream`.

## The browser floor (read this before "PDF 打不开")

This build needs JavaScript that only landed in **2025**:
`Uint8Array.prototype.toHex` / `fromBase64`, which pdf.js uses to compute a
document's fingerprint. That is the FIRST thing `getDocument` does, so a browser
without them fails on **every** PDF — and the error it throws is the unhelpful
`n.toHex is not a function`.

| browser | minimum |
|---------|---------|
| Chrome / Edge | 140 (Sep 2025) |
| Firefox | 133 (Nov 2024) |
| Safari / iOS | 18.2 (Dec 2024) |

`js/reader.js` checks for those two methods BEFORE loading the renderer and says
so in plain language, and any other pdf.js failure still falls back to the
extracted text rather than a blank pane. So the worst case is "you get the text
instead of the page", never a mystery.

**If a real user is below this floor, the fix is the legacy build**, which exists
for exactly this: swap `build/pdf.min.mjs` + `build/pdf.worker.min.mjs` for
`legacy/build/pdf.min.mjs` + `legacy/build/pdf.worker.min.mjs` (about 100 KB
larger each) and change the two paths in `js/reader.js`. That is a deliberate
trade — bigger and slightly slower, but a much wider browser floor — so make it
if someone actually hits the wall, not pre-emptively.

## What is here

| path | what it is |
|------|------------|
| `build/pdf.min.mjs` | the API (`getDocument`, `GlobalWorkerOptions`) |
| `build/pdf.worker.min.mjs` | the parser/renderer, runs in a worker |
| `cmaps/` | CID font maps — **required for Chinese/Japanese/Korean PDFs**, which otherwise render as blanks |
| `standard_fonts/` | the base-14 fonts for PDFs that do not embed them (Liberation + Foxit, with their own licenses) |
| `image_decoders/` | the JBIG2 / JPEG2000 wasm decoders used by scanned and archival PDFs |
| `iccs/` | ICC colour profiles, so colour-managed pages keep their colours |

The `.map` files and the unminified builds were left out (~7 MB of nothing the
browser needs). `js/reader.js` points pdf.js at `cmaps/`, `standard_fonts/`,
`wasmUrl` and `iccUrl` explicitly — the defaults assume a bundler and are wrong
for a plain static path.

## Upgrading

There is no script in the repo for this (`scripts/` is gitignored). Fetch the
same set from jsDelivr and update the version above:

```bash
V=6.4.0   # ← the version you are moving to
curl -s "https://data.jsdelivr.com/v1/packages/npm/pdfjs-dist@$V?structure=flat" \
  | python -c "import json,sys; print('\n'.join(f['name'] for f in json.load(sys.stdin)['files']))" \
  | grep -E '^/(build/pdf(\.worker)?\.min\.mjs|LICENSE|(cmaps|standard_fonts|image_decoders|iccs)/)'
# then download each of those paths from https://cdn.jsdelivr.net/npm/pdfjs-dist@$V/...
```

Check [pdf.js releases](https://github.com/mozilla/pdf.js/releases) for
security fixes before choosing a version, and re-copy `LICENSE`.
