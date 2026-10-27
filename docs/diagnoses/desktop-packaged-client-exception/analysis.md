# Desktop Packaged Client Exception

## Problem description

After the desktop app is packaged and opened, the window can show the Next.js
production client error screen:

```text
Application error: a client-side exception has occurred (see the browser console for more information).
```

This is a regression after the previous desktop packaging fix. That fix made
`/_next/static` CSS/JS available, but the packaged runtime still has an
uncovered production-only asset path.

## Reproduction steps

Current packaged artifact used for diagnosis:

```powershell
cd M:/takoAI/AragonTrading
Test-Path "desktop/dist/win-unpacked/Aragon Trading.exe"
Test-Path desktop/dist/win-unpacked/resources/web/web-server.json
```

Minimal server-side reproduction of the packaged web runtime:

```powershell
cd M:/takoAI/AragonTrading
$env:PORT = "18761"
$env:HOSTNAME = "127.0.0.1"
$env:NODE_ENV = "production"
node desktop/dist/win-unpacked/resources/web/server.js
```

In another shell, request the optimized logo URL used by the homepage:

```powershell
curl.exe -v "http://127.0.0.1:18761/_next/image?url=%2Fbrand%2Flogo.png&w=48&q=75"
```

Observed evidence from the packaged app log at
`%APPDATA%/aragon-desktop/logs/web.log`:

```text
Error: 'sharp' is required to be installed in standalone mode for the image optimization to function correctly.
Read more at: https://nextjs.org/docs/messages/sharp-missing-in-production
```

Additional browser automation against the same packaged standalone server
confirmed that `/` returns `200` and loads CSS/JS, while the page reports failed
resources from the production runtime. This explains why the previous
`verify:dist` check can pass the static asset layer while the real packaged
window still fails later.

## Root cause analysis

Primary root cause: the homepage uses `next/image`, but the standalone desktop
web bundle does not include `sharp`, and the Next config does not disable image
optimization for desktop packaging.

Evidence:

- [apps/web/app/page.tsx](M:/takoAI/AragonTrading/apps/web/app/page.tsx:4)
  imports `Image` from `next/image`.
- [apps/web/app/page.tsx](M:/takoAI/AragonTrading/apps/web/app/page.tsx:241)
  renders the header logo with `<Image src="/brand/logo.png" ... priority />`.
- [apps/web/package.json](M:/takoAI/AragonTrading/apps/web/package.json:12)
  lists production dependencies, and `sharp` is not present.
- [apps/web/next.config.mjs](M:/takoAI/AragonTrading/apps/web/next.config.mjs:5)
  configures `reactStrictMode`, `output`, and rewrites, but does not set
  `images.unoptimized`.
- The packaged output confirms `sharp` is absent:

```powershell
Test-Path desktop/dist/win-unpacked/resources/web/node_modules/sharp
# False
```

Why this reaches the packaged desktop but was missed earlier:

- [desktop/scripts/build-web.mjs](M:/takoAI/AragonTrading/desktop/scripts/build-web.mjs:176)
  installs only dependencies declared by `apps/web/package.json` into the
  isolated web build.
- [desktop/scripts/build-web.mjs](M:/takoAI/AragonTrading/desktop/scripts/build-web.mjs:220)
  then builds `output: "standalone"`, where Next production image optimization
  requires `sharp`.
- [desktop/scripts/verify-dist.mjs](M:/takoAI/AragonTrading/desktop/scripts/verify-dist.mjs:120)
  verifies `/_next/static/*.js`, and
  [desktop/scripts/verify-dist.mjs](M:/takoAI/AragonTrading/desktop/scripts/verify-dist.mjs:114)
  verifies `/_next/static/*.css`, but it does not request `/_next/image` and
  does not assert that the browser page has no `pageerror`.

Reviewer audit additions:

- [desktop/dist/win-unpacked/resources/web/server.js](M:/takoAI/AragonTrading/desktop/dist/win-unpacked/resources/web/server.js:12)
  is the standalone server actually started by the packaged web runtime. Its
  embedded Next config has `output: "standalone"`,
  `images.path: "/_next/image"`, and `images.unoptimized: false`.
- Requesting `/` from that server returns HTML that preloads and renders the
  logo through `/_next/image?url=%2Fbrand%2Flogo.png&w=32|48&q=75`, so the
  cited `next/image` component is not dead code; it is in the first render path
  for the packaged desktop homepage.
- The packaged runtime directory used by the reproduction,
  `desktop/dist/win-unpacked/resources/web`, has no top-level or nested
  `node_modules/sharp` and no `node_modules/@img` native sharp packages.
  Requesting the generated logo URL causes the standalone server to write the
  same `sharp is required to be installed in standalone mode` error to stderr.
- Boundary found during review: a local HTTP probe returned `200 image/png` for
  the generated logo URL even while the server logged the missing-`sharp`
  error. Therefore the `sharp` log is strong evidence of an executed,
  production-only failing path, but by itself it does not prove that the
  browser's visible "client-side exception" screen is caused solely by image
  optimization. A broken image request normally should not crash React. The
  missing evidence is a captured Electron/browser console `pageerror` stack
  from the packaged window.
- [desktop/src/main.js](M:/takoAI/AragonTrading/desktop/src/main.js:100) creates
  the `BrowserWindow`, first loads `loading.html`, and later
  [desktop/src/main.js](M:/takoAI/AragonTrading/desktop/src/main.js:284) calls
  `win.loadURL(cfg.WEB_URL)`. There is no packaged-window instrumentation here
  that records `webContents` console messages or uncaught page errors, which is
  why the current logs can show the server-side image error without a matching
  client stack.

I also inspected Electron persisted state in
`%APPDATA%/aragon-desktop/Local Storage/leveldb`. The latest `aragon-ui` entry
is valid JSON with `version: 3`, expected fields (`instId`, `bar`, `mode`,
`indicators`, `chartPane`, `bottomTab`), and no malformed value. That makes
localStorage migration a weaker explanation for the current packaged failure.

## Impact surface

- Affects packaged desktop production runtime on Windows.
- Most visible on first paint because the header logo is rendered on every page
  load with `priority`.
- Does not affect `next dev` in the same way.
- Can be missed by current release verification because CSS/JS static assets are
  available and `/` can return HTML even while image optimization fails.

## Candidate fixes

| Fix | Summary | Invasiveness | Risk | Effort |
|---|---|---:|---:|---:|
| A | Replace the header `next/image` logo with a plain `<img>` for the local static PNG. | Low | Low | Low |
| B | Set `images: { unoptimized: true }` for the desktop/standalone build path. | Low-Medium | Medium: affects all Next images if more are added later | Low |
| C | Add `sharp` as a production dependency and ensure it is packaged into `resources/web/node_modules`. | Medium | Medium: native binary dependency, platform-specific packaging risk | Medium |

Reviewer audit of candidate fixes:

- Candidate A covers the only current `next/image` import found under
  `apps/web`, and the affected image is a local static logo with fixed rendered
  size. It removes the executed `/_next/image` first-render path without
  changing global image behavior. Boundary: if future code adds another
  `next/image` before `sharp` or `images.unoptimized` is addressed, this class
  of packaging warning can return.
- Candidate B covers future `next/image` usage, but it is broader than needed
  for the current codebase and changes all image behavior for the web app
  unless it is carefully scoped to desktop packaging.
- Candidate C is a valid server-runtime fix for Next standalone image
  optimization, but it adds a native dependency to the packaged Electron
  artifact. On Windows packaging this creates extra binary, architecture, and
  tracing surface, so it is not the minimum fix for a 24x24 local PNG.
- Updating `verify-dist` to request `/_next/image` or capture browser
  `pageerror` is verification hardening, not the smallest product-code fix.
  It should be added only if the next node has enough scope for release-test
  coverage; it should not replace the product fix.

## Recommended fix

Use candidate A first: replace the local header logo `next/image` usage with a
plain `<img>` for `/brand/logo.png`.

Justification:

- The image is a 24x24 local app logo, so Next image optimization adds no
  meaningful value.
- It removes the native `sharp` requirement from the desktop runtime.
- It is smaller and less risky than adding a native dependency to the packaged
  app.
- It keeps future Next image usage decisions explicit instead of globally
  disabling optimization.

Verification follow-up: if this fix is accepted, extend
`desktop/scripts/verify-dist.mjs` so release verification opens the page in a
browser context and fails on `pageerror`/console errors. This is test coverage
for the class of failure, not a second product-code fix.

## 璇勫缁撹 (Review Verdict)

Final recommended fix: implement candidate A only as the product-code fix:
replace the homepage header logo's `next/image` component with a plain `<img>`
that points at `/brand/logo.png`, then remove the now-unused `next/image`
import.

Justification: the report's evidence does show that the packaged first render
executes a `next/image` path and that the standalone server logs the
missing-`sharp` production error from that path. Candidate A is the smallest
change that removes that executed packaging-only path, covers all current
`next/image` usage in `apps/web`, and avoids a native Windows dependency or a
global `images.unoptimized` behavior change.

Important limitation for the next node: the current evidence does not yet prove
that the `sharp` warning is the sole direct cause of the visible client-side
exception screen, because a local probe returned `200 image/png` for the logo
URL while still logging the error. After applying candidate A, manually open the
packaged desktop app and verify that the white screen is gone; if it persists,
the next investigation must capture Electron `webContents` console/pageerror
output before applying any broader fix.
