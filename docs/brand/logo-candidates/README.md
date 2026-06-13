# Aragon Trading Logo Candidates

This folder is the permanent logo candidate library for the logo redesign system.
It contains ten AI-generated PNG logo candidates and `manifest.json`, which records
the prompt, palette, scorecard, and current selected logo.

## Current Selection

`logo-07-signal-gate` is selected because it has the strongest small-size
silhouette, fits the existing dark trading workbench, and works cleanly as a
desktop icon without relying on text or chart cliches.

## Swapping Logos

1. Pick a candidate id from `manifest.json`.
2. Run the desktop icon sync command from the repository root:

   ```powershell
   python desktop/scripts/make-icons.py --candidate logo-07-signal-gate
   ```

   Or from `desktop/`:

   ```powershell
   npm run icons -- --candidate logo-07-signal-gate
   ```

3. Confirm `manifest.json` still has exactly one `selected: true` entry and that
   `selectedId` matches it.
4. Re-run the verification commands from the implementation spec.

The sync command regenerates:

- `apps/web/public/brand/logo.png`
- `apps/web/public/brand/logo-192.png`
- `apps/web/public/brand/logo-512.png`
- `apps/web/public/favicon.ico`
- `desktop/build/icon.png`
- `desktop/build/icon.ico`
- `desktop/build/icon.icns` when Pillow can write ICNS on the current platform

On Windows, `icon.png` and `icon.ico` are required. Regenerate `icon.icns` on
macOS before publishing a macOS release if the Windows run could not write it.
