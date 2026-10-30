"""从品牌源图生成 Web 与桌面壳所需的图形资源。

两份源图（均为白底、黑色细线稿）：
  - 完整标志：NE 线框 + 「NexEdu」字标   → 欢迎页主视觉
  - 仅 NE 标记：                          → 侧栏、标题栏、图标

产出：
  web/assets/logo-full.png   完整标志（透明底深蓝细线），用于欢迎页
  web/assets/logo-ne.png     NE 标记（更淡的细线稿），用于侧栏与标题栏
  web/favicon.ico            多尺寸图标（浏览器标签页）
  assets/icon.ico            exe / 任务栏图标

小尺寸为什么单独处理：源图线宽仅 1px，缩到 20~30px 时线条覆盖率极低，
过淡看不见、过度加深又会糊成一团黑块，因此标记与图标使用不同浓度。

用法（需 Pillow）：
  python tools/make_logo_assets.py --full "<完整标志源图>" --mark "<NE 源图>"
  python tools/make_logo_assets.py "<完整标志源图>"     # 自动切分 NE 与字标
"""

import argparse
import io
import os
import struct

from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INK = (24, 32, 56)          # #182038，与界面主色一致
FULL_GAIN = 1.9             # 完整标志：欢迎页显示 188px，细线足够清晰
MARK_GAIN = 1.6             # NE 标记：界面显示 19~31px，保持淡雅细线观感
MARK_DILATE = 0             # 标记不加粗

# 图标按目标尺寸分别调档：(加粗半径, alpha 增益)。
# 源图是 1px 细线稿，缩放后线条覆盖率随尺寸急剧下降：不加粗则小尺寸淡到看不见，
# 一律加粗又会在小尺寸糊成黑块（任务栏图标此前发黑就是这个原因）。
# 因此 16~48px（任务栏/列表实际使用的尺寸）微加粗 1px 并适度提高对比，
# 64px 以上保持与源图一致的淡雅细线。
ICON_TUNE = {
    16: (1, 3.8), 24: (1, 3.2), 32: (1, 2.8),
    48: (1, 2.2), 64: (0, 2.0), 128: (0, 1.7), 256: (0, 1.5),
}
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_PAD = 0.94             # 图案占画布比例（图标用完整标志，尽量占满画布）


def _to_ink(img, gain):
    """白底黑线 → 透明底深蓝线（用明暗度生成 alpha，保留抗锯齿）。"""
    gray = img.convert("L")

    def _curve(v):
        a = int((255 - v) * gain)
        return 0 if a < 14 else min(255, a)

    out = Image.new("RGBA", gray.size, INK + (0,))
    out.putalpha(gray.point(_curve))
    return out


def _dilate(img, px):
    """按需加粗线稿：对 alpha 做最大值滤波。"""
    if px <= 0:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").filter(ImageFilter.MaxFilter(px * 2 + 1)))
    return out


def _trim(img, pad=6):
    """裁掉四周留白，保留少量边距。"""
    box = img.getbbox()
    if not box:
        return img
    x0, y0, x1, y1 = box
    return img.crop((max(0, x0 - pad), max(0, y0 - pad),
                     min(img.width, x1 + pad), min(img.height, y1 + pad)))


def _split(ink):
    """按行空白把整幅标志拆成 NE 标记与 NexEdu 字标（依据 alpha 通道）。"""
    alpha = ink.getchannel("A")
    w, h = alpha.size
    px = alpha.load()
    rows = []
    for y in range(h):
        dark = 0
        for x in range(0, w, 2):
            if px[x, y] > 14:
                dark += 1
        rows.append(dark)
    filled = [y for y, d in enumerate(rows) if d > 0]
    if not filled:
        return ink, ink
    top, bottom = filled[0], filled[-1]
    gap_start, best = None, None
    for y in range(top, bottom + 1):
        if rows[y] == 0:
            if gap_start is None:
                gap_start = y
        else:
            if gap_start is not None:
                run = y - gap_start
                if run >= 8 and (best is None or run > best[2]):
                    best = (gap_start, y, run)
                gap_start = None
    if best is None:
        return ink.crop((0, top, w, bottom + 1)), Image.new("RGBA", (1, 1))
    return (ink.crop((0, top, w, best[0])),
            ink.crop((0, best[1], w, bottom + 1)))


def _fit_square(mark, side):
    """把 NE 标记等比缩放并居中放入正方形画布（留边），不改变浓度。"""
    canvas = Image.new("RGBA", (side, side), INK + (0,))
    inner = int(side * ICON_PAD)
    scale = min(inner / mark.width, inner / mark.height)
    w = max(1, int(mark.width * scale))
    h = max(1, int(mark.height * scale))
    art = mark.resize((w, h), Image.LANCZOS)
    canvas.paste(art, ((side - w) // 2, (side - h) // 2), art)
    return canvas


def _boost_alpha(img, gain):
    """按目标尺寸提升 alpha 对比（用于小尺寸图标）。"""
    if gain <= 1.0:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(
        lambda v: 0 if v == 0 else min(255, int(v * gain))))
    return out


def _icon_images(mark_hires):
    """生成各尺寸图标：按尺寸决定加粗半径与对比增益。"""
    out = []
    for s in ICON_SIZES:
        dilate, gain = ICON_TUNE.get(s, (0, 1.6))
        art = _fit_square(_dilate(mark_hires, dilate), s)
        out.append(_boost_alpha(art, gain))
    return out


def _write_ico(path, images):
    """写出多尺寸 ICO（PNG 条目，与 Pillow 的写法一致，便于按尺寸定制）。"""
    entries, blobs, offset = b"", [], 6 + 16 * len(images)
    for img in images:
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        w = 0 if img.width >= 256 else img.width
        h = 0 if img.height >= 256 else img.height
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        blobs.append(data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(images)))
        f.write(entries)
        for data in blobs:
            f.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", help="完整标志源图（旧用法）")
    ap.add_argument("--full", help="完整标志源图：NE + 字标")
    ap.add_argument("--mark", help="NE 标记源图（仅 NE）")
    args = ap.parse_args()

    full_src = args.full or args.source
    if not full_src:
        ap.error("请提供完整标志源图（位置参数或 --full）")

    # 完整标志（欢迎页主视觉）
    full = _trim(_to_ink(Image.open(full_src), FULL_GAIN))
    if not args.mark:
        # 未单独提供 NE 源图：从完整标志里切出标记与字标，再按原比例拼回
        mark_raw, word = _split(_to_ink(Image.open(full_src), FULL_GAIN))
        mark_raw, word = _trim(mark_raw, 8), _trim(word, 8)
        space = max(10, int(word.height * 0.30))
        w = max(mark_raw.width, word.width)
        full = Image.new("RGBA",
                         (w, mark_raw.height + space + word.height), INK + (0,))
        full.paste(mark_raw, ((w - mark_raw.width) // 2, 0), mark_raw)
        full.paste(word, ((w - word.width) // 2, mark_raw.height + space), word)

    # NE 标记（侧栏 / 标题栏）：单独的源图更干净，保持淡雅细线
    mark_src = args.mark or full_src
    mark = _dilate(_trim(_to_ink(Image.open(mark_src), MARK_GAIN), pad=8),
                   MARK_DILATE)

    # 图标：用「完整标志」（NE + NexEdu 字标）——exe 与任务栏都显示它；
    # 侧栏/标题栏用的仍是只有 NE 的 logo-ne.png。按尺寸调档见 ICON_TUNE。
    icon_src = _trim(_to_ink(Image.open(full_src), 1.0), pad=4)

    web_assets = os.path.join(ROOT, "web", "assets")
    os.makedirs(web_assets, exist_ok=True)
    pngs = [(os.path.join(web_assets, "logo-full.png"), full),
            (os.path.join(web_assets, "logo-ne.png"), mark)]
    ico_paths = [os.path.join(ROOT, "web", "favicon.ico"),
                 os.path.join(ROOT, "assets", "icon.ico")]
    for path, img in pngs:
        img.save(path, "PNG", optimize=True)
    icons = _icon_images(icon_src)
    for path in ico_paths:
        _write_ico(path, icons)

    for path in [p for p, _ in pngs] + ico_paths:
        print(f"{os.path.relpath(path, ROOT):30} {os.path.getsize(path) / 1024:7.1f} KB")
    print(f"完整标志 {full.size} · NE 标记 {mark.size} · 图标 {list(ICON_SIZES)}")


if __name__ == "__main__":
    main()
