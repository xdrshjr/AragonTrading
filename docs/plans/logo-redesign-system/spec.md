# Aragon Trading Logo Redesign System Specification

**Version**: v2

## 评审记录

- **P1（已修复）- Verification commands referenced a non-existent package filter**: the original plan used `pnpm --filter @aragon/web ...`, but the actual package is `aragon-web` and repository-standard orchestration is `pnpm test`, `cd apps/web; pnpm test`, and `cd apps/web; pnpm build`. The testing section now uses commands that match `package.json`.
- **P1（已修复）- Runtime logo format was ambiguous**: the original architecture allowed `logo.svg` even though the requirement says logos must be AI-generated images rather than code-drawn vector marks. The runtime web contract now uses generated PNG derivatives only, with fixed dimensions in the header.
- **P1（已修复）- Desktop icon regeneration path could accidentally preserve the old generated "A" icon**: the original text allowed keeping `desktop/scripts/make-icons.py` as a fallback. Because that script currently draws the old icon procedurally, the implementation plan now requires replacing its behavior or adding a new selected-logo sync script and repointing `npm run icons`.
- **P1（已修复）- `.icns` acceptance was stricter than the Windows implementation environment can guarantee**: the original acceptance criteria required regenerated `.icns` even on Windows. The plan now makes Windows `.ico` and `.png` mandatory, keeps `.icns` best-effort on Windows, and requires macOS regeneration before a macOS release.
- **P2（记录）- Candidate selection remains partly subjective**: the scorecard reduces the risk, but final taste is still subjective. This is acceptable because the user explicitly asked the agent to choose the best-looking candidate and preserve the other nine for later swaps.

## Overview

Aragon Trading currently presents its brand through plain text in the web header and a deterministic, script-drawn desktop icon set. The product is now mature enough to need a more polished identity: ten distinct, top-tier, flat logo concepts generated through AI image creation, all preserved in a dedicated asset library, with one selected winner integrated into the web UI and desktop packaging surface. This work is intentionally not a marketing landing-page redesign; it is a focused brand asset replacement that must leave the trading workflow, OKX data contracts, simulation behavior, and release pipeline unchanged.

The requirement has two equally important outcomes. First, the project must retain all ten candidate logo assets in a stable folder so the user can swap between them later without regenerating art or hunting through external files. Second, the chosen best logo must be wired into every active product surface where the current logo/icon is visible: the Next.js web header, favicon/app metadata if added, Electron development window icon, Windows `.ico`, Linux `.png`, and macOS `.icns` packaging paths. The visual direction should be restrained and premium, closer to AI infrastructure companies such as OpenAI, Anthropic, or xAI/Grok than to colorful fintech clip art: flat, minimal, low-color, high-recognition marks with strong silhouettes at small sizes.

This specification is the implementation handoff for the downstream engineer. It deliberately does not create image assets or modify source files in this node. The downstream implementation should use an AI image generation capability for the ten source logos, not code-drawn vector art or a Pillow-only generator. Code may be written only to organize assets, convert formats, and wire the chosen generated asset into the application.

## Technical Design

### Architecture

The implementation should introduce a small brand asset system with three layers:

1. **Candidate library**: all ten original AI-generated logo concepts stored under `docs/brand/logo-candidates/` with metadata explaining each concept, prompt, palette, source file, and selection notes.
2. **Runtime brand assets**: the selected logo exported into application-consumable PNG/ICO/ICNS derivatives under `apps/web/public/brand/`, `apps/web/public/favicon.ico`, and `desktop/build/`.
3. **Integration points**: Web header and metadata consume generated PNG assets from `apps/web/public/brand/`; Electron and electron-builder continue consuming `desktop/build/icon.png`, `desktop/build/icon.ico`, and `desktop/build/icon.icns`.

The downstream engineer should keep the source-of-truth candidate images separate from generated runtime derivatives. Candidate files are for review and future replacement; runtime files are optimized for the product. This prevents future logo swaps from becoming ambiguous: choose another candidate, update the metadata `selected` flag, regenerate derivatives, then rerun the same verification commands.

### Logo Generation Requirements

Generate ten distinct logo concepts using AI image generation. Each concept must be a square image, minimum `1024x1024`, with a transparent background if the generator supports it. If transparent output is not available, use a clean solid neutral background and remove or mask it during export. Do not generate text-heavy logos; the mark must work without spelling "Aragon Trading". A subtle `A`, angular signal shape, market grid, orbit, aperture, or abstract trading/strategy motif is acceptable, but the mark must not look like a candlestick chart icon from a template pack.

The ten concepts should intentionally vary:

| Candidate | Direction | Palette Limit | Notes |
|---|---|---:|---|
| `logo-01-orbital-a` | Abstract orbital "A" monogram | 2 colors | AI-company style, very simple silhouette |
| `logo-02-market-aperture` | Aperture/ring with implied signal axis | 2 colors | Premium, geometric, strong at favicon size |
| `logo-03-strategy-node` | Three-node strategy graph inside a compact mark | 2 colors | Quant systems without looking like a network logo clone |
| `logo-04-folded-alpha` | Folded alpha/triangle form | 1-2 colors | Minimal and high-contrast |
| `logo-05-calm-wave` | Single wave/curve suggesting equity curve | 2 colors | Avoid literal chart bars |
| `logo-06-symmetric-knot` | Interlocking neutral knot | 1 color | OpenAI-like restraint without copying |
| `logo-07-signal-gate` | Gateway/portal mark with negative space | 2 colors | Good desktop icon candidate |
| `logo-08-precision-compass` | Compass/crosshair abstraction | 2 colors | Trading precision, not military |
| `logo-09-liquid-grid` | Soft grid folded into an emblem | 2 colors | More modern SaaS feel |
| `logo-10-monoline-a` | Monoline "A" with hidden tick/signal | 1 color | Most restrained option |

All concepts must avoid excessive color. The approved palette range is near-black, off-white, muted graphite, soft silver, muted teal, or one restrained accent. No rainbow gradients, no glossy 3D, no cartoon mascots, no over-detailed candlesticks, and no stock exchange clichés. The selected winner should be the concept that remains legible at `16x16`, feels premium in the existing dark UI, and scales cleanly into desktop `.ico`.

### Sequence Of Operations

1. Inspect the current UI and icon paths: `apps/web/app/page.tsx`, `apps/web/app/layout.tsx`, `apps/web/package.json`, `desktop/src/main.js`, `desktop/electron-builder.yml`, `desktop/package.json`, `desktop/build/`, and `desktop/scripts/make-icons.py`.
2. Generate ten logo images with the AI image tool, using the concept directions above. Save the full-resolution originals as PNG files under `docs/brand/logo-candidates/`.
3. Create `docs/brand/logo-candidates/manifest.json` with an entry for each candidate: `id`, `displayName`, `sourcePng`, `prompt`, `palette`, `styleNotes`, `strengths`, `weaknesses`, and `selected`.
4. Choose the best candidate using a documented scorecard: small-size legibility, originality, fit with Aragon Trading, fit with dark UI, packaging suitability, and low-color discipline. Mark only one candidate as `selected: true`.
5. Export the selected winner into app-ready derivatives. Create `apps/web/public/` and `apps/web/public/brand/` if they do not already exist:
   - `apps/web/public/brand/logo.png` at `512x512`
   - `apps/web/public/brand/logo-192.png`
   - `apps/web/public/brand/logo-512.png`
   - `apps/web/public/favicon.ico`
   - `desktop/build/icon.png`
   - `desktop/build/icon.ico`
   - `desktop/build/icon.icns`
6. Update the web header to render an image mark before "Aragon Trading", preserving the current dense trading-workbench layout and avoiding a marketing-style hero or large decorative brand block.
7. Update `apps/web/app/layout.tsx` metadata icons so browser tabs and installed app surfaces use the new favicon assets.
8. Review `desktop/scripts/make-icons.py`. Its current behavior procedurally draws the old "A" icon, so downstream implementation must either replace this file with a conversion script that derives icon formats from the selected generated PNG, or add a new script such as `scripts/sync-logo-assets.py` and update `desktop/package.json` so `npm run icons` uses the selected generated PNG. The final desktop build must not regenerate the old code-drawn icon.
9. Run targeted verification: `cd apps/web; pnpm test`, `cd apps/web; pnpm build`, `cd desktop; npm run icons`, `cd desktop; npm run check`, and a visual check of the header and favicon. The repository-level `pnpm test` may be used as the broader orchestration command when dependencies are installed.

## File / Module Change Plan

| File or directory | Change | Intent |
|---|---|---|
| `docs/brand/logo-candidates/` | Create | Permanent library containing all ten AI-generated logo originals and review metadata. |
| `docs/brand/logo-candidates/logo-01-orbital-a.png` through `logo-10-monoline-a.png` | Create | Store the ten distinct candidate logos; never delete non-selected candidates. |
| `docs/brand/logo-candidates/manifest.json` | Create | Track prompts, visual notes, selection state, and future swap instructions. |
| `docs/brand/logo-candidates/README.md` | Create | Human-readable instructions for reviewing candidates and swapping the selected logo. |
| `apps/web/public/brand/` | Create | Runtime web logo assets derived from the selected candidate. |
| `apps/web/public/favicon.ico` | Create or replace | Browser tab icon generated from the selected candidate. |
| `apps/web/app/page.tsx` | Modify | Replace the plain text-only header brand with an image mark plus existing product name. |
| `apps/web/app/layout.tsx` | Modify | Add Next.js metadata icon entries for favicon and app icons. |
| `apps/web/package.json` | Modify only if needed | Add an asset sync script only if the implementation uses a repeatable conversion command; do not rename the package from `aragon-web`. |
| `desktop/build/icon.png` | Replace | Electron dev window/Linux icon from selected AI-generated logo. |
| `desktop/build/icon.ico` | Replace | Windows executable and installer icon. |
| `desktop/build/icon.icns` | Replace | macOS packaging icon, generated on a compatible toolchain or documented as best effort. |
| `desktop/scripts/make-icons.py` | Modify or supersede | Ensure running icon generation no longer restores the old deterministic placeholder. This is required because the current script draws the previous icon in code. |
| `desktop/package.json` | Modify if script names change | Point `npm run icons` at the selected-logo conversion flow and keep `npm run check` working. |
| `desktop/README.md` | Modify | Document how to regenerate desktop icon formats from the selected candidate. |

## Interface Design

No REST, WebSocket, database, trading strategy, or backend API interface changes are required. The public user-facing interface change is visual: the header brand presentation becomes an image mark plus "Aragon Trading", and browser/desktop icons use the selected generated logo.

If an asset sync CLI is added, expose it as a local developer command only:

| Command | Purpose |
|---|---|
| `cd apps/web; pnpm build` | Verify the web app still builds with new public assets. |
| `cd apps/web; pnpm test` | Run the existing Vitest suite after header/metadata changes. |
| `cd desktop; npm run icons` | Regenerate desktop `icon.png`, `icon.ico`, and best-effort `icon.icns` from the selected generated logo. |
| `python scripts/sync-logo-assets.py --candidate logo-07-signal-gate` | Optional direct command to switch candidates and regenerate runtime derivatives. |

The optional `sync-logo-assets.py` should read `docs/brand/logo-candidates/manifest.json`, validate that the requested candidate exists, update the single selected entry, and write the app/desktop derivatives. It should fail fast if the source PNG is missing, smaller than `1024x1024`, visually blank, or cannot be converted into `.ico`.

## Data Model

No production database schema changes are required. The only new structured data is the candidate manifest:

```json
{
  "version": 1,
  "selectedId": "logo-07-signal-gate",
  "candidates": [
    {
      "id": "logo-07-signal-gate",
      "displayName": "Signal Gate",
      "sourcePng": "logo-07-signal-gate.png",
      "prompt": "AI-generated prompt used for the source image",
      "palette": ["#0B0D12", "#E7ECEF", "#5FA8A3"],
      "styleNotes": "Flat geometric gate with negative-space signal axis",
      "strengths": ["clear at 16x16", "premium dark UI fit"],
      "weaknesses": ["accent may need dark-mode contrast tuning"],
      "selected": true
    }
  ]
}
```

The manifest must enforce one selected candidate. If a future implementation adds a script, the script should treat `selectedId` and the `selected` booleans as a consistency check: exactly one candidate must match both.

## Testing & Acceptance Criteria

Acceptance criteria:

1. `docs/brand/logo-candidates/` contains exactly ten distinct AI-generated candidate PNGs plus `manifest.json` and `README.md`.
2. The ten candidates are visually different, flat, premium, low-color, and not code-drawn.
3. The selected logo is documented in the manifest and all non-selected candidates remain in place.
4. `apps/web/app/page.tsx` shows the selected logo in the header without disrupting the existing trading controls or dense workbench layout.
5. Browser favicon and app icons resolve from the selected logo.
6. `desktop/build/icon.png`, `desktop/build/icon.ico`, and `desktop/build/icon.icns` are regenerated from the same selected logo, not from the old deterministic icon script.
7. Running the documented icon regeneration command preserves the selected generated logo.
8. Web verification passes: `cd apps/web; pnpm test` and `cd apps/web; pnpm build`, or repository-level `pnpm test` if using the root orchestration script.
9. Desktop static verification passes: `cd desktop; npm run check`; if icon conversion scripts are added, their syntax/runtime validation is included.
10. Manual visual QA confirms the header mark is crisp at normal desktop scale, favicon is recognizable, and Windows taskbar/exe icon uses the new mark after packaging.
11. On Windows, `desktop/build/icon.png` and `desktop/build/icon.ico` must be regenerated successfully from the selected generated image. `desktop/build/icon.icns` is best-effort on Windows, but a macOS release is blocked until `icon.icns` is regenerated and verified on macOS.

The visual QA should include screenshots at desktop width and a narrow viewport. The logo must not overlap controls, enlarge the header height unexpectedly, or introduce layout shift during image load. Use explicit image dimensions and an `alt` value such as `"Aragon Trading"` in the header.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| AI generator produces raster artifacts or text fragments | Logo looks unprofessional at small sizes | Reject candidates with artifacts; use transparent background; downsample and inspect at `16x16`, `32x32`, and `48x48`. |
| Logo too colorful or decorative | Conflicts with premium AI-company style requested | Keep palette to one neutral base plus at most one muted accent; document palette per candidate. |
| Runtime assets overwrite candidate originals | Future swaps lose source material | Store originals only in `docs/brand/logo-candidates/`; generated derivatives live under app/desktop paths. |
| Existing `make-icons.py` restores old code-drawn icon | Selected AI logo is accidentally replaced later | Modify or supersede the script so the selected candidate is the input source; update `npm run icons`. |
| `.icns` generation fails on Windows | macOS package icon remains stale | Treat `.ico` and `.png` as mandatory on Windows; document `.icns` as best-effort on Windows and require macOS regeneration before a macOS release. |
| Header image causes layout shift | Trading workbench feels unstable | Use fixed width/height, preserve header spacing, and keep the text label unchanged. |
| Candidate selection is subjective | Downstream engineer cannot justify choice | Use the scorecard and record selected rationale in `manifest.json` and README. |

## Implementation Notes For Downstream Engineer

The implementation should be a narrow brand asset change. Do not alter trading APIs, OKX fallback behavior, strategy logic, chart behavior, run history, simulation sessions, or desktop process orchestration. Keep all new source code deterministic and small: the only non-deterministic creative step is AI image generation of the ten source logos. After selecting the winning mark, every app-visible derivative should come from that single source image so the system has one clear brand identity while preserving the other nine candidates for future swaps.

## 评审结论

**通过**。P1 concerns have been resolved in v2 of this specification. No P0 or unresolved P1 issues remain; downstream implementation can proceed against the corrected paths, commands, and icon-generation contract above.
