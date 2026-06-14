# Problem description

The desktop build can start the packaged frontend server and return the page
HTML, but the UI appears as unstyled text. The symptom is consistent with the
Next standalone server running without the matching `.next/static` assets:
HTML is served, but CSS/JS asset URLs under `/_next/static/...` return 404 or
point at stale files.

This showed up after recent UI work because the page now depends heavily on
Tailwind-generated layout utilities in `apps/web/components/Chart/ChartWorkspace.tsx`.
If the package contains the standalone server but misses the matching static
assets, the renderer still has text nodes but loses the layout, colors, spacing,
and client-side bundles.

# Reproduction steps

Minimal deterministic repro of the failure mode, using the freshly generated
standalone output before `build-web.mjs` assembles static assets:

```powershell
cd M:/takoAI/AragonTrading/desktop
npm run build:web
# If interrupted after `.web-build/.next/standalone` exists but before assemble
# completes, or for direct repro, run the raw standalone server:
cd M:/takoAI/AragonTrading/desktop/.web-build/.next/standalone
$env:PORT='19862'
$env:HOSTNAME='127.0.0.1'
$env:NODE_ENV='production'
node server.js
```

In another PowerShell:

```powershell
$home = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:19862/
$home.StatusCode
([regex]::Matches($home.Content, '/_next/static/css/[^"'']+\.css'))[0].Value
Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:19862$(([regex]::Matches($home.Content, '/_next/static/css/[^"'']+\.css'))[0].Value)"
```

Observed during diagnosis:

```text
HTML_STATUS=200 CONTENT_TYPE=text/html; charset=utf-8 LENGTH=14715
CSS_REF_COUNT=3
FIRST_CSS=/_next/static/css/9231f38b7be4ccc4.css
CSS_ERROR=remote server returned error: (404) Not Found
```

Control check against the existing assembled package:

```powershell
cd M:/takoAI/AragonTrading/desktop/dist/win-unpacked/resources/web
$env:PORT='19861'
$env:HOSTNAME='127.0.0.1'
$env:NODE_ENV='production'
node server.js
```

The existing `dist/win-unpacked/resources/web` artifact returned CSS 200 in
this workspace, so the checked artifact here is not currently reproducing the
visible failure. It is also stale relative to the partial fresh build:

```text
desktop/.web-build/.next/BUILD_ID                    vj81XSaN2FEd7UkLCLemt
desktop/resources/web/.next/BUILD_ID                 3OrMH9U-vvAAAbzEmBw7_
desktop/dist/win-unpacked/resources/web/.next/BUILD_ID 3OrMH9U-vvAAAbzEmBw7_
```

# Root cause analysis

Primary hypothesis: the packaged desktop app is being built or launched from a
Next standalone tree whose `.next/static` directory is missing or does not match
the server build ID.

Evidence:

- Next standalone does not include static assets by itself; this project knows
  that and manually copies them after the standalone copy in
  `desktop/scripts/build-web.mjs:225` to `desktop/scripts/build-web.mjs:229`.
- The raw standalone output serves `/` successfully but returns 404 for the CSS
  URL referenced by that HTML. That directly reproduces the "can load but only
  text / no styling" symptom.
- `desktop/scripts/build-web.mjs:220` to `desktop/scripts/build-web.mjs:223`
  removes `desktop/resources/web`, recreates it, and copies the standalone
  server tree before static assets are copied. Only later does
  `desktop/scripts/build-web.mjs:226` to `desktop/scripts/build-web.mjs:229`
  copy `.next/static`. If `staticSrc` is absent, the script currently logs only
  a warning at `desktop/scripts/build-web.mjs:230` to
  `desktop/scripts/build-web.mjs:231` rather than failing the build, and it can
  still write `web-server.json` at `desktop/scripts/build-web.mjs:239` to
  `desktop/scripts/build-web.mjs:243`.
- Electron resolves production web code from `resources/web/web-server.json` and
  starts that `server.js` with its directory as cwd via
  `desktop/src/lib/paths.js:52` to `desktop/src/lib/paths.js:63`.
- `electron-builder` packages `desktop/resources/web` into the app at
  `desktop/electron-builder.yml:18` to `desktop/electron-builder.yml:21`, so any
  stale or incomplete `resources/web` state is copied verbatim.
- `desktop/package.json:16` exposes `pack` as `electron-builder --dir`, which
  does not run `build:web`. `desktop/package.json:17` has the safer `smoke`
  script, but the unsafe direct script remains available.
- `desktop/scripts/verify-dist.mjs:248` to `desktop/scripts/verify-dist.mjs:252`
  starts the packaged standalone server; `desktop/scripts/verify-dist.mjs:261`
  to `desktop/scripts/verify-dist.mjs:269` verifies only that `/` returns HTML
  containing an app marker; and `desktop/scripts/verify-dist.mjs:272` to
  `desktop/scripts/verify-dist.mjs:280` verifies only the BFF health route. It
  does not parse the HTML and fetch referenced CSS/JS assets, so a "text-only"
  app can pass the current web-home check.

Reviewer counter-checks and limits:

- The existing `desktop/dist/win-unpacked/resources/web` artifact in this
  workspace returned CSS 200 during the original diagnosis, and
  `desktop/resources/web/.next/static` is present. This is a counter-example to
  "all packaged artifacts are currently broken"; the supported conclusion is
  narrower: the build/package pipeline permits a runnable packaged server whose
  static assets are missing or stale, and current verification may not catch it.
- The raw standalone repro is not itself the production launch path; its value
  is that it proves the exact failure mode produced when a Next standalone
  server runs without the matching `.next/static` directory. The production path
  evidence is the combination of `resources/web` packaging, manifest-based
  server startup, and missing static-asset verification cited above.
- The prior task hashes `50a0d42481bef76d692c831b508f240d234bc063` and
  `cd9b394f34912355d68dd91f5e7272483bbfe0b4` were not present in the visible
  Git object database during review, so this audit does not rely on those
  commits for causality. The current source citations are sufficient for the
  packaging/static-assets failure chain.

Relevant recent UI surface:

- `apps/web/components/Chart/ChartWorkspace.tsx:99` to
  `apps/web/components/Chart/ChartWorkspace.tsx:142` introduced the desktop
  chart workspace layout classes. These classes are present in freshly generated
  CSS, so Tailwind scanning is not the root cause.

# Impact surface

- Affects packaged desktop builds and portable `win-unpacked` launches.
- Does not affect normal `apps/web` dev mode when Next serves its own static
  assets.
- User-visible result is severe: the desktop app can appear to "load" while the
  UI is effectively broken, because HTML text renders without layout, styling,
  chart sizing, or normal client-side behavior.
- Current release verification may miss this class of regression because it
  checks page HTML and BFF health but not static asset availability.

# Candidate fixes

| Option | Fix | Invasiveness | Risk | Effort |
|---|---|---:|---:|---:|
| A | Make `build-web.mjs` assemble into a temporary directory, copy `.next/static` and `public`, validate required assets, then atomically replace `resources/web`. | Medium | Low | Medium |
| B | Extend `verify-dist.mjs` to parse `/`, request every referenced `/_next/static/*.css` and critical JS chunk, and fail on non-200 or wrong content-type. | Low | Low | Low |
| C | Remove or guard unsafe direct packaging paths: make `pack` run `build:backend build:web` first, or fail `pack` when `resources/web/.next/BUILD_ID` is older than `.web-build/.next/BUILD_ID` / missing static assets. | Medium | Medium | Low |

# Recommended fix

Implement a single fail-closed static-asset packaging gate, combining the
minimum necessary parts of A, B, and C:

1. Make `build-web.mjs` assemble into a temporary directory, require
   `.next/static`, copy `public`, validate that the manifest-resolved server has
   adjacent static assets, then atomically replace `resources/web`.
2. Make the normal `pack` path run the web build before `electron-builder --dir`
   or rename the current direct command to an explicit `pack:only` escape hatch.
   Otherwise users can still package stale `resources/web` without invoking the
   fixed assembler.
3. Extend `verify-dist.mjs` to parse `/`, request referenced
   `/_next/static/*.css` and critical JS chunks, and fail on non-200 or wrong
   content type.

These changes are one pipeline fix rather than feature work: build-time
validation prevents half-assembled output, the packaging script prevents stale
output from being selected, and release verification catches the user-visible
symptom if either boundary regresses.

The smallest robust acceptance criterion is:

1. `npm run build:web` leaves `desktop/resources/web/.next/static` present next
   to the manifest-resolved `server.js`.
2. `npm run verify:dist` starts the packaged server, fetches `/`, extracts all
   `/_next/static/*.css` references, and requires each CSS response to be `200`
   with `text/css`.
3. `npm run verify:dist` also fetches the critical app JS chunks referenced by
   the page and requires `200` JavaScript responses.

## 评审结论

Verdict: the root-cause direction is valid, but the original recommendation was
too narrow if implemented as only atomic assembly plus verification. The
evidence shows three connected boundaries in the failing path: `build-web.mjs`
can produce or leave a runnable `resources/web` without failing on missing
static assets, `electron-builder` copies that directory verbatim, and the
current `pack` script can bypass `build:web` entirely.

Final recommended fix: implement the fail-closed static-asset packaging gate
described above. It is the minimum robust fix because each boundary covers a
distinct counter-example: atomic validated assembly covers interrupted or
incomplete `build:web`; safe `pack` covers stale `resources/web`; and
`verify-dist` asset fetching covers future regressions by testing the actual CSS
and JS URLs that make the packaged UI styled and interactive.

## Issues Found During Implementation

- While validating the new `verify-dist` static asset checks, a stale
  `next start -p 18761` process listening on IPv6 `::` returned old HTML with
  static asset hashes that did not exist in the packaged artifact. The existing
  `portFree()` default checked only `127.0.0.1`, so the precheck could miss an
  IPv6/dual-stack listener. The implementation therefore also updates the
  default port bind probe to bind without a host, making the verification gate
  fail before startup when either IPv4 or IPv6 already owns the port.
