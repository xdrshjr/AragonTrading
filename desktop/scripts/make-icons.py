"""make-icons —— 确定性生成 Aragon Trading 品牌图标集（spec: desktop-release-acceptance v2, §4.4 / G5）。

替换前两轮遗留的 ≈1.2 KB 占位 build/icon.{ico,png,icns}。设计决策 D5：用 Pillow 在品牌深色底
（#0b0d12，与窗口 backgroundColor 一致）上画圆角块 + 单字母「A」标——**纯几何绘制、不依赖任何字体文件**，
故任何纯编码 agent 都可复现、可重跑（被否方案：手绘 PNG 二进制 blob 不可复现、不可审计）。

用法：python scripts/make-icons.py   （改图标时手动跑，或 `npm run icons`）

产物（写入 desktop/build/）：
  · icon.png   256×256 品牌 PNG（linux icon、窗口 dev 期 icon、文档预览）
  · icon.ico   多分辨率 [16,24,32,48,64,128,256]（electron-builder win.icon，配置零改动）
  · icon.icns  尽力导出（mac；Pillow 写出受限，失败则保留占位并提示在 mac 上重生成，R6/R-icns）

注：build-time 工具，不写入 requirements.txt（同 pyinstaller 的处理）；Pillow 缺失则尝试 pip 安装。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ---- Pillow 依赖（构建期工具，按需安装，不入 requirements.txt）----
try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - 仅首次缺失时触发
    print("[make-icons] 未检测到 Pillow，尝试 pip 安装 …")
    subprocess.run([sys.executable, "-m", "pip", "install", "pillow"], check=True)
    from PIL import Image, ImageDraw

# ---- 品牌参数 ----
BG = (11, 13, 18, 255)        # #0b0d12 —— 与 BrowserWindow.backgroundColor 一致
CLAY = (217, 119, 87, 255)    # #D97757 —— Anthropic 暖陶土色，作圆角块底
MASTER = 1024                 # 主画布尺寸；各目标尺寸由此 LANCZOS 下采样，保证小尺寸仍锐利
SIZES = [16, 24, 32, 48, 64, 128, 256]

BUILD_DIR = Path(__file__).resolve().parent.parent / "build"


def render_master() -> "Image.Image":
    """在 MASTER×MASTER 画布上绘制：深色底 + 圆角陶土块 + 负空间「A」字标。"""
    img = Image.new("RGBA", (MASTER, MASTER), BG)
    d = ImageDraw.Draw(img)

    # 圆角块（陶土底）
    pad = 96
    x0, y0, x1, y1 = pad, pad, MASTER - pad, MASTER - pad
    d.rounded_rectangle([x0, y0, x1, y1], radius=180, fill=CLAY)

    # 「A」字标：以底色画三笔（左腿 / 右腿 / 横梁）——两腿间三角空间自然留陶土色，读作 A。
    stroke = 90
    apex = (512, 266)
    bl = (306, 758)
    br = (722, 758)
    # 横梁落在两腿约 70% 高度处（视觉平衡）
    t = 0.701
    cross_l = (apex[0] + (bl[0] - apex[0]) * t, apex[1] + (bl[1] - apex[1]) * t)
    cross_r = (apex[0] + (br[0] - apex[0]) * t, apex[1] + (br[1] - apex[1]) * t)

    d.line([apex, bl], fill=BG, width=stroke)
    d.line([apex, br], fill=BG, width=stroke)
    d.line([cross_l, cross_r], fill=BG, width=stroke)
    # 圆化笔触端点（apex / 两脚），避免下采样后出现生硬方头
    r = stroke // 2
    for cx, cy in (apex, bl, br):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BG)

    return img


def main() -> int:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    master = render_master()

    # icon.png —— 256
    png_path = BUILD_DIR / "icon.png"
    master.resize((256, 256), Image.LANCZOS).save(png_path, format="PNG")
    print(f"[make-icons] ✓ 写 {png_path.name}（256×256）")

    # icon.ico —— 多分辨率（Pillow 由 256 源自动下采样各尺寸）
    ico_path = BUILD_DIR / "icon.ico"
    src256 = master.resize((256, 256), Image.LANCZOS)
    src256.save(ico_path, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"[make-icons] ✓ 写 {ico_path.name}（多分辨率 {SIZES}）")

    # icon.icns —— 尽力（R6/R-icns）；失败保留占位并提示 mac 重生成
    icns_path = BUILD_DIR / "icon.icns"
    try:
        master.resize((512, 512), Image.LANCZOS).save(icns_path, format="ICNS")
        print(f"[make-icons] ✓ 写 {icns_path.name}（512×512，尽力）")
    except Exception as exc:  # noqa: BLE001 - icns 写出受限属预期，不阻断 Windows 验收
        print(
            f"[make-icons] ⚠ 生成 {icns_path.name} 失败（{exc}）。"
            " 保留既有占位；mac 正式产物须在 mac 上重生成（R-icns）。"
        )

    print("[make-icons] 完成。Windows 验收用 icon.ico（win.icon 路径不变，仅换内容）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
