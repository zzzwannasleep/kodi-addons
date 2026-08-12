# -*- coding: utf-8 -*-
"""Generate the root-menu tile images.

Estuary's grid views print no per-item label, so a menu entry that is only a
name would render as a blank tile. The server's own library artwork solves this
by baking the name into a 16:9 image; these tiles match that treatment for the
five entries the plugin adds itself.

Run: python tools/make_menu_tiles.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "plugin.video.uhd", "resources", "media")
W, H = 960, 540

# name, english subtitle, accent colour
TILES = [
    ("resume", "继续观看", "Continue Watching", (232, 93, 74)),
    ("nextup", "接着看", "Next Up", (86, 156, 214)),
    ("latest", "最近添加", "Recently Added", (96, 178, 128)),
    ("favorites", "我的收藏", "Favourites", (214, 158, 72)),
    ("search", "搜索", "Search", (150, 128, 210)),
]

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf", r"D:\Kodi\media\Fonts\arial.ttf",
]
LATIN_CANDIDATES = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf"]


def font(paths, size):
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def tile(cn, en, accent):
    img = Image.new("RGB", (W, H), (14, 16, 22))
    d = ImageDraw.Draw(img)
    # Diagonal wash in the accent colour, dark enough to keep text readable.
    for y in range(H):
        for_t = y / (H - 1)
        base = (int(14 + accent[0] * 0.20 * (1 - for_t)),
                int(16 + accent[1] * 0.20 * (1 - for_t)),
                int(22 + accent[2] * 0.20 * (1 - for_t)))
        d.line([(0, y), (W, y)], fill=base)
    d.polygon([(W, 0), (W, H), (W - 300, H)], fill=(
        int(accent[0] * 0.30), int(accent[1] * 0.30), int(accent[2] * 0.30)))
    d.rectangle([0, H - 6, W, H], fill=accent)

    f_cn = font(FONT_CANDIDATES, 118 if len(cn) <= 3 else 96)
    f_en = font(LATIN_CANDIDATES, 34)
    d.text((70, 190), cn, font=f_cn, fill=(245, 247, 250))

    # English subtitle inside an outlined pill, echoing the server artwork.
    box = d.textbbox((0, 0), en, font=f_en)
    tw, th = box[2] - box[0], box[3] - box[1]
    x0, y0 = 74, 350
    d.rounded_rectangle([x0, y0, x0 + tw + 56, y0 + th + 34], radius=8,
                        outline=(235, 238, 244), width=3)
    d.text((x0 + 28, y0 + 14), en, font=f_en, fill=(235, 238, 244))

    badge = font(LATIN_CANDIDATES, 26)
    d.text((W - 96, H - 62), "UHD", font=badge, fill=(210, 214, 222))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, cn, en, accent in TILES:
        path = os.path.join(OUT, "menu_%s.jpg" % name)
        tile(cn, en, accent).save(path, quality=88)
        print("  %-28s %s" % (os.path.basename(path), Image.open(path).size))


if __name__ == "__main__":
    main()
