"""Sync Aragon Trading logo assets from the selected AI-generated candidate.

The source-of-truth logo library lives in docs/brand/logo-candidates. This
script reads manifest.json, validates that exactly one candidate is selected,
and derives all runtime web and desktop icon assets from that generated PNG.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - first-run convenience for local tooling
    print("[make-icons] Pillow not found; installing build-time dependency...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pillow"], check=True)
    from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "brand" / "logo-candidates" / "manifest.json"
CANDIDATE_DIR = MANIFEST_PATH.parent
WEB_BRAND_DIR = ROOT / "apps" / "web" / "public" / "brand"
WEB_FAVICON = ROOT / "apps" / "web" / "public" / "favicon.ico"
DESKTOP_BUILD_DIR = ROOT / "desktop" / "build"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Missing logo manifest: {MANIFEST_PATH}")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def select_candidate(manifest: dict, candidate_id: str | None) -> dict:
    candidates = manifest.get("candidates", [])
    if not candidates:
        raise ValueError("Logo manifest has no candidates.")

    if candidate_id:
        matches = [item for item in candidates if item.get("id") == candidate_id]
        if len(matches) != 1:
            raise ValueError(f"Unknown logo candidate: {candidate_id}")
        manifest["selectedId"] = candidate_id
        for item in candidates:
            item["selected"] = item.get("id") == candidate_id
        save_manifest(manifest)

    selected_id = manifest.get("selectedId")
    selected = [item for item in candidates if item.get("selected") is True]
    if len(selected) != 1:
        raise ValueError("Logo manifest must contain exactly one selected candidate.")
    if selected[0].get("id") != selected_id:
        raise ValueError("Logo manifest selectedId does not match selected candidate.")
    return selected[0]


def load_source(candidate: dict) -> Image.Image:
    source = CANDIDATE_DIR / str(candidate.get("sourcePng", ""))
    if not source.exists():
        raise FileNotFoundError(f"Selected logo source is missing: {source}")

    image = Image.open(source).convert("RGBA")
    width, height = image.size
    if width < 1024 or height < 1024:
        raise ValueError(f"Selected logo source must be at least 1024x1024: {source}")

    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError(f"Selected logo source appears blank: {source}")
    opaque_pixels = sum(1 for value in alpha.getdata() if value > 32)
    if opaque_pixels < int(width * height * 0.01):
        raise ValueError(f"Selected logo source has too little visible artwork: {source}")

    return image


def fit_square(image: Image.Image, size: int, padding_ratio: float = 0.12) -> Image.Image:
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError("Cannot resize a blank image.")

    cropped = image.crop(bbox)
    max_art = int(size * (1 - padding_ratio * 2))
    cropped.thumbnail((max_art, max_art), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(cropped, ((size - cropped.width) // 2, (size - cropped.height) // 2))
    return canvas


def save_pngs(source: Image.Image) -> None:
    WEB_BRAND_DIR.mkdir(parents=True, exist_ok=True)
    DESKTOP_BUILD_DIR.mkdir(parents=True, exist_ok=True)

    fit_square(source, 512).save(WEB_BRAND_DIR / "logo.png", format="PNG")
    fit_square(source, 192).save(WEB_BRAND_DIR / "logo-192.png", format="PNG")
    fit_square(source, 512).save(WEB_BRAND_DIR / "logo-512.png", format="PNG")
    fit_square(source, 256).save(DESKTOP_BUILD_DIR / "icon.png", format="PNG")


def save_ico(source: Image.Image) -> None:
    icon = fit_square(source, 256)
    icon.save(
        WEB_FAVICON,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
    )
    icon.save(
        DESKTOP_BUILD_DIR / "icon.ico",
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
    )


def save_icns(source: Image.Image) -> None:
    icns_path = DESKTOP_BUILD_DIR / "icon.icns"
    try:
        fit_square(source, 1024).save(icns_path, format="ICNS")
        print(f"[make-icons] Wrote {icns_path.as_posix()}")
    except Exception as exc:  # noqa: BLE001 - Pillow ICNS writing is platform-sensitive.
        print(
            "[make-icons] WARNING: Could not write icon.icns on this platform "
            f"({exc}). Regenerate on macOS before a macOS release.",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync runtime logo assets.")
    parser.add_argument(
        "--candidate",
        help="Optional logo candidate id to select before regenerating assets.",
    )
    args = parser.parse_args()

    manifest = load_manifest()
    candidate = select_candidate(manifest, args.candidate)
    source = load_source(candidate)

    save_pngs(source)
    save_ico(source)
    save_icns(source)

    print(f"[make-icons] Selected {candidate['id']}")
    print(f"[make-icons] Wrote {(WEB_BRAND_DIR / 'logo.png').as_posix()}")
    print(f"[make-icons] Wrote {WEB_FAVICON.as_posix()}")
    print(f"[make-icons] Wrote {(DESKTOP_BUILD_DIR / 'icon.png').as_posix()}")
    print(f"[make-icons] Wrote {(DESKTOP_BUILD_DIR / 'icon.ico').as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
