/**
 * Regression tests for the winCodeSign cache seeder.
 *
 * Guards the pure path/URL logic that decides *where* the seeded artifact must land and
 * *whether* a cache is already usable — the parts that, if wrong, silently reintroduce
 * the "Cannot create symbolic link" packaging failure on a non-elevated Windows host.
 *
 * Run: node --test scripts/ensure-wincodesign.test.mjs
 */

import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  resolveCacheBase,
  resolveArtifactDir,
  isSeeded,
  resolveDownloadUrl,
} from "./ensure-wincodesign.mjs";

test("resolveCacheBase prefers ELECTRON_BUILDER_CACHE override", () => {
  const base = resolveCacheBase({ ELECTRON_BUILDER_CACHE: "D:/eb-cache" });
  assert.equal(base, path.resolve("D:/eb-cache"));
});

test("resolveCacheBase falls back to LOCALAPPDATA/electron-builder/Cache", () => {
  const base = resolveCacheBase({ LOCALAPPDATA: "C:/Users/x/AppData/Local" });
  assert.equal(base, path.join("C:/Users/x/AppData/Local", "electron-builder", "Cache"));
});

test("resolveArtifactDir targets the versioned winCodeSign folder app-builder expects", () => {
  const dir = resolveArtifactDir({ ELECTRON_BUILDER_CACHE: "/cache" });
  assert.equal(dir, path.join(path.resolve("/cache"), "winCodeSign", "winCodeSign-2.6.0"));
});

test("isSeeded is true only when the rcedit binary is present", () => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "wcs-test-"));
  try {
    assert.equal(isSeeded(tmp), false);
    mkdirSync(path.join(tmp, "darwin"), { recursive: true });
    assert.equal(isSeeded(tmp), false, "darwin dir alone must not count as seeded");
    writeFileSync(path.join(tmp, "rcedit-x64.exe"), "stub");
    assert.equal(isSeeded(tmp), true);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("resolveDownloadUrl uses the official GitHub release by default", () => {
  const url = resolveDownloadUrl({});
  assert.equal(
    url,
    "https://github.com/electron-userland/electron-builder-binaries/releases/download/" +
      "winCodeSign-2.6.0/winCodeSign-2.6.0.7z"
  );
});

test("resolveDownloadUrl honors ELECTRON_BUILDER_BINARIES_MIRROR", () => {
  const url = resolveDownloadUrl({
    ELECTRON_BUILDER_BINARIES_MIRROR: "https://mirror.example/binaries/",
  });
  assert.equal(url, "https://mirror.example/binaries/winCodeSign-2.6.0/winCodeSign-2.6.0.7z");
});

test("resolveDownloadUrl honors the full download override URL", () => {
  const url = resolveDownloadUrl({
    ELECTRON_BUILDER_BINARIES_DOWNLOAD_OVERRIDE_URL: "https://host/dl",
  });
  assert.equal(url, "https://host/dl/winCodeSign-2.6.0.7z");
});
