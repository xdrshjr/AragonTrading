# Desktop Packaging Fails — winCodeSign Symlink EPERM

## Problem description

`npm run dist` (and the `pack.ps1` one-click) fails on Windows during the
`electron-builder` step. The build never produces an installer; instead it loops
on a 7za extraction error and exits non-zero:

```text
⨯ cannot execute  cause=exit status 2
ERROR: Cannot create symbolic link : 客户端没有所需的特权 :
  ...\electron-builder\Cache\winCodeSign\<id>\darwin\10.12\lib\libcrypto.dylib
ERROR: Cannot create symbolic link : 客户端没有所需的特权 :
  ...\electron-builder\Cache\winCodeSign\<id>\darwin\10.12\lib\libssl.dylib
• Above command failed, retrying 3 more times
```

This is distinct from the earlier `build:web` EPERM symlink issue (already fixed
via the isolated flat `.web-build/` copy) and from the runtime client-exception
diagnosis. This one happens at the **packaging** stage, not the web build and not
at runtime.

## Reproduction steps

```powershell
cd M:/takoAI/AragonTrading/desktop
# resources/{backend,web,agent} already built
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\electron-builder\Cache\winCodeSign" -ErrorAction SilentlyContinue
npm run dist:only
```

Preconditions that make it reproduce (this machine):

- Windows, standard user (no admin).
- Developer Mode OFF (`HKLM\...\AppModelUnlock\AllowDevelopmentWithoutDevLicense`
  absent), so `fs.symlinkSync` fails with `EPERM`.

`npm run pack` (`electron-builder --dir`) hits the same path because it also
embeds the exe icon/version.

## Root cause analysis

The trigger is **not** code signing (the project configures none — the log says
`no signing info identified, signing is skipped`). The trigger is `rcedit`.

Verified by instrumenting `node_modules`:

- `getSignVendorPath()` (the JS path that would fetch `winCodeSign` for signing)
  is **never called** — confirmed `signUsingSigntool` returns early at
  `windowsSignToolManager.js` ("no signing info ... skipped") before any vendor
  path resolution.
- Instrumenting `executeAppBuilder` showed the `winCodeSign` download is kicked
  off immediately by the `rcedit` command:

  ```text
  DEBUG_APPBUILDER_CMD ["rcedit","--args","[... --set-icon ...icon.ico]"]
  • downloading winCodeSign-2.6.0.7z
  ```

Why `rcedit` needs `winCodeSign`: app-builder ships `rcedit-x64.exe` /
`rcedit-ia32.exe` **inside** the `winCodeSign-2.6.0.7z` artifact (they sit at the
archive root). `electron-builder` runs `rcedit` to stamp the packaged exe's icon
and version strings (`winPackager.signAndEditResources`), so it downloads and
extracts `winCodeSign` even with signing disabled.

Why extraction fails: the archive contains two macOS symlinks
(`darwin/10.12/lib/libcrypto.dylib → libcrypto.1.0.0.dylib` and the `libssl`
equivalent). app-builder extracts with `7za x` in symlink-restoring mode; on a
non-elevated Windows host without Developer Mode the process lacks
`SeCreateSymbolicLinkPrivilege`, so 7za exits with code `2`, app-builder treats
that as failure, retries, and the build ultimately fails. The two macOS symlinks
are irrelevant to a Windows build.

Confirming the boundary:

- `--config.win.signAndEditExecutable=false` makes the `winCodeSign` download
  disappear (proving the trigger is the sign/edit-executable flow), but it also
  skips icon/version embedding — not an acceptable fix.
- Pre-extracting `winCodeSign-2.6.0.7z` into
  `…\Cache\winCodeSign\winCodeSign-2.6.0\` while ignoring the two symlink
  failures, then running `electron-builder`, yields: `winCodeSign` re-download
  count `0`, zero symlink errors, exit `0`, full nsis + portable + blockmap.

## Impact surface

- Affects Windows desktop packaging (`dist`, `pack`, `smoke`) on any machine
  without admin rights or Developer Mode — i.e. the project's stated "no
  elevation" target environment.
- Independent of network: the archive downloads fine; only extraction fails.

## Recommended fix (implemented)

Add an idempotent, Windows-only pre-step that seeds the cache so app-builder
skips its own failing extraction:

- `desktop/scripts/ensure-wincodesign.mjs` — downloads `winCodeSign-2.6.0.7z`
  via app-builder's own downloader (keeps the existing proxy/mirror behavior),
  extracts it into the exact cache folder, tolerates the two expected macOS
  symlink failures, and asserts `rcedit-x64.exe` is present. No-op when already
  seeded or on non-Windows.
- Wired into `dist` and `pack` (`package.json` `run-s` chains) and into
  `pack.ps1` (which invokes `electron-builder` directly). Added
  `check:ensurewincodesign` and a `node:test` suite (`*.test.mjs`).

This mirrors the repo's existing "stage a Windows-symlink-free copy" approach for
`build:web`, and keeps packaging working without admin / Developer Mode.

Maintenance note: the seeder pins `ARTIFACT = "winCodeSign-2.6.0"`. If
electron-builder is upgraded and resolves a newer `winCodeSign` version, bump
that constant (the symlink error would otherwise resurface).

## Separate latent issue (not this failure)

`electron-builder.yml` gives both the `nsis` and `portable` targets the same
`artifactName` (`${productName}-${version}-${arch}.${ext}`), so the two installers
collide on one `Aragon Trading-0.1.0-x64.exe` and the second target overwrites
the first. The build still exits `0`, so this is not the reported failure, but it
means only one of the two installer types survives. Worth giving each target a
distinct `artifactName` in a follow-up.
