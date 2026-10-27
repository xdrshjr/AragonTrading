/**
 * Pre-seed the electron-builder `winCodeSign` cache on Windows so that `npm run dist`
 * (and `pack`) succeed without administrator rights or Developer Mode.
 *
 * Why this is needed:
 *   electron-builder embeds the packaged exe icon / version strings via app-builder's
 *   `rcedit` command. `rcedit-x64.exe` ships *inside* the `winCodeSign-2.6.0.7z`
 *   artifact, so app-builder downloads and extracts that archive even when no code
 *   signing is configured. The archive contains two macOS symlinks
 *   (`darwin/10.12/lib/libcrypto.dylib`, `libssl.dylib`). app-builder extracts with
 *   7za in symlink-restoring mode, and on Windows a standard user lacks
 *   SeCreateSymbolicLinkPrivilege (granted only by admin or Developer Mode), so 7za
 *   exits with code 2, app-builder retries and ultimately the whole build fails. The
 *   two macOS symlinks are irrelevant to a Windows build.
 *
 * The fix:
 *   Download the archive ourselves (via app-builder's own downloader, so the existing
 *   proxy / mirror setup keeps working) and extract it into the exact cache folder
 *   electron-builder looks for, tolerating the two unavoidable symlink failures. With a
 *   populated cache, app-builder skips its own failing extraction and packaging
 *   succeeds. This mirrors the repo's existing "stage a Windows-symlink-free copy"
 *   approach used by build:web.
 *
 * Idempotent and Windows-only: a no-op on macOS/Linux and when the cache is already
 * seeded.
 */

import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync, unlinkSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { appBuilderPath } = require("app-builder-bin");
const { path7za } = require("7zip-bin");

// Pinned to the artifact electron-builder 25.x / app-builder-bin resolves for Windows.
// If electron-builder is upgraded and a newer winCodeSign version is requested, the
// symlink-extraction error will resurface during packaging — bump this constant then.
const ARTIFACT = "winCodeSign-2.6.0";

// rcedit (the only piece a Windows build actually consumes) sits at the archive root.
// Its presence is our "cache is usable" signal.
const SEEDED_MARKER = "rcedit-x64.exe";

const DEFAULT_BINARIES_BASE_URL =
  "https://github.com/electron-userland/electron-builder-binaries/releases/download/";

/** Resolve the electron-builder cache base, honoring the ELECTRON_BUILDER_CACHE override. */
export function resolveCacheBase(env) {
  const override = env.ELECTRON_BUILDER_CACHE;
  if (override && override.trim()) {
    return path.resolve(override.trim());
  }
  const localAppData = env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
  return path.join(localAppData, "electron-builder", "Cache");
}

/** Directory app-builder expects the extracted winCodeSign artifact to live in. */
export function resolveArtifactDir(env) {
  return path.join(resolveCacheBase(env), "winCodeSign", ARTIFACT);
}

/** True when the cached artifact already contains the rcedit binary we need. */
export function isSeeded(artifactDir) {
  return existsSync(path.join(artifactDir, SEEDED_MARKER));
}

/** Build the artifact download URL, honoring electron-builder's mirror env vars. */
export function resolveDownloadUrl(env) {
  const overrideUrl = env.ELECTRON_BUILDER_BINARIES_DOWNLOAD_OVERRIDE_URL;
  if (overrideUrl) {
    return `${overrideUrl}/${ARTIFACT}.7z`;
  }
  const baseUrl = env.ELECTRON_BUILDER_BINARIES_MIRROR || DEFAULT_BINARIES_BASE_URL;
  return `${baseUrl}${ARTIFACT}/${ARTIFACT}.7z`;
}

/** Download the artifact archive using app-builder's downloader (proven proxy/network path). */
function downloadArchive(archivePath, env) {
  const url = resolveDownloadUrl(env);
  console.log(`[ensure-wincodesign] downloading ${url}`);
  const result = spawnSync(
    appBuilderPath,
    ["download", "--url", url, "--output", archivePath],
    { stdio: ["ignore", "inherit", "inherit"] }
  );
  if (result.status !== 0) {
    throw new Error(
      `app-builder download failed (exit ${result.status}) for ${url}. ` +
        `Check network/proxy or set ELECTRON_BUILDER_BINARIES_MIRROR.`
    );
  }
}

/**
 * Extract the archive into destDir, tolerating the two macOS-symlink failures that are
 * expected on a non-elevated Windows host. Success is asserted by the presence of the
 * rcedit binary rather than by the 7za exit code.
 */
function extractArchive(archivePath, destDir) {
  mkdirSync(destDir, { recursive: true });
  // Capture (don't inherit) 7za output so the expected macOS-symlink failures don't
  // surface as alarming red errors; we summarize them ourselves and only dump the raw
  // output on a genuinely fatal exit code.
  const result = spawnSync(
    path7za,
    ["x", "-y", "-bd", `-o${destDir}`, archivePath],
    { encoding: "utf8" }
  );
  // 7za exit code 2 == "warning (non-fatal)": the only failures here are the macOS
  // symlinks we do not use on Windows. Any other non-zero (1, 7, 8) is a real error.
  if (result.status !== 0 && result.status !== 2) {
    console.error(result.stdout || "");
    console.error(result.stderr || "");
    throw new Error(`7za extraction failed (exit ${result.status}) for ${archivePath}.`);
  }
  if (result.status === 2) {
    console.log(
      "[ensure-wincodesign] skipped 2 macOS dylib symlinks (expected on Windows; unused by Windows builds)."
    );
  }
  if (!isSeeded(destDir)) {
    console.error(result.stdout || "");
    console.error(result.stderr || "");
    throw new Error(
      `Extraction did not produce ${SEEDED_MARKER} in ${destDir}; archive may be corrupt.`
    );
  }
}

export function main() {
  if (process.platform !== "win32") {
    console.log("[ensure-wincodesign] non-Windows host, nothing to do.");
    return;
  }

  const env = process.env;
  const artifactDir = resolveArtifactDir(env);
  if (isSeeded(artifactDir)) {
    console.log(`[ensure-wincodesign] cache already seeded: ${artifactDir}`);
    return;
  }

  // Drop any partial extraction left by a previous failed app-builder run so we start clean.
  if (existsSync(artifactDir)) {
    rmSync(artifactDir, { recursive: true, force: true });
  }

  const archivePath = path.join(path.dirname(artifactDir), `${ARTIFACT}.7z`);
  mkdirSync(path.dirname(archivePath), { recursive: true }); // download target may not exist yet on a fresh machine
  try {
    downloadArchive(archivePath, env);
    extractArchive(archivePath, artifactDir);
  } finally {
    if (existsSync(archivePath)) {
      unlinkSync(archivePath);
    }
  }
  console.log(`[ensure-wincodesign] seeded cache: ${artifactDir}`);
}

// Run only when executed directly (`node ensure-wincodesign.mjs`), not when imported by tests.
if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
