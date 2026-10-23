#!/usr/bin/env python3
"""Pixel preview of the SH1106 idle/clock screen (128x64).

Replays the draw calls of renderIdle() in src/mono_display.cpp using the real
Adafruit glcdfont and the real bitmaps from include/mono_weather_icons.h, then
writes a PNG with the standard library only (no Pillow needed).

Why this exists: the panel has no antialiasing and hard 128x64 limits, so a
1-px mistake is obvious on hardware but invisible in a diff. Run this before
flashing any layout change and show the PNG to the user for approval.

The constants in the LAYOUT block below MIRROR renderIdle(). If you move a
block there, move it here too, or the preview will lie. The script prints the
computed coordinates and fails if anything leaves the panel.

Usage:
    python tools/render_idle_preview.py                  # 4 states -> preview/idle.png
    python tools/render_idle_preview.py --date-format 5  # long format (size-1 fallback)
    python tools/render_idle_preview.py --all-formats    # one panel per date format
    python tools/render_idle_preview.py --no-weather --12h --ampm AM
    python tools/render_idle_preview.py --scale 8 --out /tmp/idle.png

Requires the Adafruit GFX library to be installed: run `pio run` once (or
`pio pkg install`) so .pio/libdeps/<env>/.../glcdfont.c exists.

Panel order in the PNG (default run):
    1. date + clock + weather   (three blocks stacked edge to edge)
    2. date + clock             (weather off)
    3. clock + weather          (date hidden: original anchors)
    4. clock                    (date hidden, weather off)
--hide-date keeps only the two date-hidden states; --no-weather drops every
weather row; --all-formats draws one panel per date format.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import date as Date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- LAYOUT: mirrors renderIdle() -------------------------------------------
W, H = 128, 64
CLOCK_SIZE = 3
CLOCK_GLYPH_H = 7 * CLOCK_SIZE   # 21 px of real glyph at size 3
WX_ICON_W = WX_ICON_H = 30       # mono_weather.h
WX_ICON_TOP = 34                 # 30 px icon on the last 30 rows (34..63)
WX_ICON_X = 23
WX_TEMP_X = 60
WX_TEMP_SIZE = 2
WX_TEMP_DY = 8                   # temp baseline offset below the icon
DATE_MAX_SIZE = 2                # same scale as the weather temperature
DATE_ROW = 0
# Anchors kept for the date-hidden screen (regression guard).
CLK_Y_WX_OLD, CLK_Y_OLD = 5, 20
WX_TOP_OLD, WX_TEMP_Y_OLD = 32, 40

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

ICON_BY_CODE = {
    "01d": "wi_day_clear", "01n": "wi_night_clear",
    "02d": "wi_day_cloud", "02n": "wi_night_cloud",
    "03d": "wi_cloud", "03n": "wi_cloud",
    "04d": "wi_very_cloudy", "04n": "wi_very_cloudy",
    "09d": "wi_showers", "09n": "wi_showers",
    "10d": "wi_rain", "10n": "wi_rain",
    "11d": "wi_storm", "11n": "wi_storm",
    "13d": "wi_snow", "13n": "wi_snow",
    "50d": "wi_fog", "50n": "wi_fog",
}


# --- Assets ------------------------------------------------------------------
def find_font() -> Path:
    candidates = sorted(ROOT.glob(".pio/libdeps/*/*/glcdfont.c"))
    if not candidates:
        sys.exit("glcdfont.c not found: run `pio run` (or `pio pkg install`) "
                 "once so PlatformIO downloads Adafruit GFX.")
    return candidates[0]


def load_font() -> bytes:
    src = find_font().read_text(encoding="utf-8", errors="ignore")
    body = src[src.index("{") + 1:src.rindex("}")]
    data = bytes(int(x, 16) for x in re.findall(r"0[xX]([0-9a-fA-F]{2})", body))
    if len(data) < 256 * 5:
        sys.exit(f"unexpected glcdfont.c size: {len(data)} bytes")
    return data


def load_icons() -> dict[str, bytes]:
    src = (ROOT / "include" / "mono_weather_icons.h").read_text(
        encoding="utf-8", errors="ignore")
    icons: dict[str, bytes] = {}
    for m in re.finditer(
            r"const unsigned char\s+(?:PROGMEM\s+)?(\w+)\s*\[\][^=]*=\s*\{(.*?)\}",
            src, re.S):
        out = bytearray()
        for tok in re.findall(r"0[xX][0-9a-fA-F]{2}|B[01]{8}", m.group(2)):
            out.append(int(tok, 16) if tok.lower().startswith("0x")
                       else int(tok[1:], 2))
        icons[m.group(1)] = bytes(out)
    if "degree_bmp" not in icons:
        sys.exit("degree_bmp not found in include/mono_weather_icons.h")
    return icons


# --- GFX emulation -----------------------------------------------------------
class OLED:
    """Just enough of Adafruit_GFX for the idle screen: default font + bitmaps."""

    def __init__(self, font: bytes):
        self.font = font
        self.p = [[0] * W for _ in range(H)]

    def px(self, x: int, y: int) -> None:
        if 0 <= x < W and 0 <= y < H:
            self.p[y][x] = 1

    def text(self, t: str, x: int, y: int, size: int = 1) -> None:
        # Default font: 5 columns per glyph, bit 0 = top row, 6*size advance.
        for ch in t:
            base = (ord(ch) & 0xFF) * 5
            for i in range(5):
                line = self.font[base + i]
                for j in range(8):
                    if line & (1 << j):
                        for a in range(size):
                            for b in range(size):
                                self.px(x + i * size + a, y + j * size + b)
            x += 6 * size

    def bmp(self, x: int, y: int, data: bytes, w: int, h: int) -> None:
        stride = (w + 7) // 8
        for j in range(h):
            for i in range(w):
                if data[j * stride + i // 8] & (0x80 >> (i % 8)):
                    self.px(x + i, y + j)


# --- Layout (mirrors renderIdle) ---------------------------------------------
def text_width(t: str, size: int) -> int:
    """Adafruit getTextBounds width for the default font: 6*size per char."""
    return 6 * size * len(t)


def format_date(fmt: int, d: Date) -> str:
    day, mon, year = d.day, d.month, d.year
    return (
        f"{day:02d}.{mon:02d}.{year:04d}",
        f"{day:02d}-{mon:02d}-{year:04d}",
        f"{mon:02d}/{day:02d}/{year:04d}",
        f"{year:04d}-{mon:02d}-{day:02d}",
        f"{day:02d} {MONTHS[mon - 1]} {year:04d}",
        f"{MONTHS[mon - 1]} {day:02d}, {year:04d}",
    )[fmt]


def date_scale(text: str) -> int:
    """Same rule as renderIdle: size 2, drop to size 1 only if it won't fit."""
    for size in (DATE_MAX_SIZE, 1):
        if text_width(text, size) <= W:
            return size
    return 1


@dataclass
class Scene:
    label: str
    date: str | None
    dsize: int
    date_h: int
    clk_y: int
    wx: bool
    wx_top: int
    wx_temp_y: int


def build_scene(label: str, date_fmt: int, hide_date: bool, wx: bool,
                day: Date) -> Scene:
    if hide_date:
        return Scene(label, None, 0, 0,
                     CLK_Y_WX_OLD if wx else CLK_Y_OLD, wx,
                     WX_TOP_OLD, WX_TEMP_Y_OLD)
    text = format_date(date_fmt, day)
    dsize = date_scale(text)
    date_h = 7 * dsize
    gap = (WX_ICON_TOP if wx else H) - date_h - CLOCK_GLYPH_H
    clk_y = date_h + (gap // 2 if gap > 0 else 0)
    return Scene(label, text, dsize, date_h, clk_y, wx,
                 WX_ICON_TOP, WX_ICON_TOP + WX_TEMP_DY)


# --- Rendering ---------------------------------------------------------------
def draw_scene(o: OLED, sc: Scene, time_s: str, twelve: bool, ampm: str,
               temp: str, icon: bytes, degree: bytes) -> None:
    if sc.date:
        o.text(sc.date, (W - text_width(sc.date, sc.dsize)) // 2, DATE_ROW,
               sc.dsize)
    if twelve:
        # Centered time + AM/PM block, exactly as renderIdle does it.
        w = text_width(time_s, CLOCK_SIZE)
        x0 = max(0, (W - (w + 6 + 12)) // 2)
        o.text(time_s, x0, sc.clk_y, CLOCK_SIZE)
        o.text(ampm, x0 + w + 6, sc.clk_y + 8, 1)
    else:
        o.text(time_s, 20, sc.clk_y, CLOCK_SIZE)
    if sc.wx:
        n = len(temp)
        o.bmp(WX_ICON_X, sc.wx_top, icon, WX_ICON_W, WX_ICON_H)
        o.text(temp, WX_TEMP_X, sc.wx_temp_y, WX_TEMP_SIZE)
        deg_x = WX_TEMP_X + 2 + n * 12
        o.bmp(deg_x, sc.wx_temp_y, degree, 8, 10)
        o.text("C", deg_x + 9, sc.wx_temp_y, WX_TEMP_SIZE)


def write_png(path: Path, canvases: list[OLED], scale: int, gap: int) -> None:
    """Grayscale PNG, stdlib only. 1 px gray frame shows the 128x64 bounds."""
    gap = max(gap, 4)
    slot_w = W + 2
    slot_h = H + 2
    width = len(canvases) * slot_w * scale + (len(canvases) - 1) * gap * scale
    height = slot_h * scale
    img = [[255] * width for _ in range(height)]

    for k, o in enumerate(canvases):
        ox = k * (slot_w + gap) * scale + scale          # 1 px frame offset
        oy = scale
        for y in range(H):
            for x in range(W):
                if o.p[y][x]:
                    for a in range(scale):
                        for b in range(scale):
                            img[oy + y * scale + a][ox + x * scale + b] = 0
        # Frame just outside the panel: exact 128x64 limits, content untouched.
        top, bottom = oy - 1, oy + H * scale
        left, right = ox - 1, ox + W * scale
        for x in range(left, right + 1):
            img[top][x] = 170
            img[bottom][x] = 170
        for y in range(top, bottom + 1):
            img[y][left] = 170
            img[y][right] = 170

    raw = b"".join(b"\x00" + bytes(row) for row in img)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" +
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) +
        chunk(b"IDAT", zlib.compress(raw, 9)) +
        chunk(b"IEND", b""))


# --- Checks ------------------------------------------------------------------
def check_scene(sc: Scene) -> list[str]:
    errs = []
    if sc.date and text_width(sc.date, sc.dsize) > W:
        errs.append(f"date {sc.date!r} is {text_width(sc.date, sc.dsize)} px wide")
    if sc.clk_y < 0 or sc.clk_y + CLOCK_GLYPH_H > H:
        errs.append(f"clock rows {sc.clk_y}..{sc.clk_y + CLOCK_GLYPH_H - 1} "
                    "leave the panel")
    if sc.date and sc.clk_y < sc.date_h:
        errs.append("clock overlaps the date")
    if sc.wx and sc.wx_top + WX_ICON_H > H:
        errs.append(f"weather icon rows {sc.wx_top}.."
                    f"{sc.wx_top + WX_ICON_H - 1} leave the panel")
    return errs


def firmware_drift_warnings() -> list[str]:
    """Warn if the LAYOUT constants no longer match renderIdle()."""
    src = (ROOT / "src" / "mono_display.cpp").read_text(
        encoding="utf-8", errors="ignore")
    pairs = [
        (r"const int clkH\s*=\s*(\d+)", CLOCK_GLYPH_H, "clkH"),
        (r"const int wxTop\s*=\s*(\d+)", WX_ICON_TOP, "wxTop"),
        (r"clkY = wxShown \? (\d+) : (\d+);", (CLK_Y_WX_OLD, CLK_Y_OLD),
         "date-hidden clock anchors"),
        (r"wxIconY = dateShown \? wxTop : (\d+);", WX_TOP_OLD, "old icon top"),
        (r"wxTempY = dateShown \? wxTop \+ (\d+) : (\d+);",
         (WX_TEMP_DY, WX_TEMP_Y_OLD), "weather temp offsets"),
    ]
    warns = []
    for pattern, expected, name in pairs:
        m = re.search(pattern, src)
        if not m:
            warns.append(f"could not find {name} in renderIdle()")
            continue
        found = tuple(int(g) for g in m.groups()) if len(m.groups()) > 1 \
            else int(m.group(1))
        if found != expected:
            warns.append(f"{name}: firmware {found} vs preview {expected}")
    return warns


# --- Main --------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Render the SH1106 idle screen to a PNG (no Pillow).")
    ap.add_argument("--date-format", type=int, choices=range(6), default=0,
                    help="0=DD.MM.YYYY 1=DD-MM-YYYY 2=MM/DD/YYYY 3=YYYY-MM-DD "
                         "4=DD MMM YYYY 5=MMM DD, YYYY (default 0)")
    ap.add_argument("--date", type=Date.fromisoformat, default=Date(2025, 12, 31),
                    metavar="YYYY-MM-DD", help="date to render (default 2025-12-31)")
    ap.add_argument("--hide-date", action="store_true",
                    help="time-only screen: date hidden (original anchors)")
    ap.add_argument("--no-weather", action="store_true",
                    help="drop the weather row from every panel")
    ap.add_argument("--all-formats", action="store_true",
                    help="one panel per date format")
    ap.add_argument("--time", default="12:34", help="time text (default 12:34)")
    ap.add_argument("--12h", dest="twelve", action="store_true",
                    help="12-hour layout + AM/PM")
    ap.add_argument("--ampm", choices=("AM", "PM"), default="PM")
    ap.add_argument("--temp", default="21", help="weather temp text (default 21)")
    ap.add_argument("--icon", default="01d",
                    help="OpenWeatherMap icon code, e.g. 01d, 10n (default 01d)")
    ap.add_argument("--scale", type=int, default=5, help="pixels per OLED px")
    ap.add_argument("--gap", type=int, default=14, help="gap between panels")
    ap.add_argument("--out", type=Path, default=ROOT / "preview" / "idle.png",
                    help="output PNG (default preview/idle.png)")
    args = ap.parse_args()

    font = load_font()
    icons = load_icons()
    icon = icons.get(ICON_BY_CODE.get(args.icon, "wi_error"), icons["wi_error"])
    degree = icons["degree_bmp"]

    scenes: list[Scene] = []
    if args.all_formats:
        for fmt in range(6):
            scenes.append(build_scene(f"format {fmt}", fmt, False,
                                      not args.no_weather, args.date))
    else:
        hides = (True,) if args.hide_date else (False, True)
        weathers = (False,) if args.no_weather else (True, False)
        for hide in hides:
            for wx in weathers:
                if hide:
                    label = "clock + weather" if wx else "clock only"
                else:
                    label = "date + clock + weather" if wx else "date + clock"
                scenes.append(build_scene(label, args.date_format, hide, wx,
                                          args.date))

    canvases = []
    failed = False
    print(f"{'scene':<26} {'date':<13} {'sz':>2} {'clk_y':>5} "
          f"{'wx rows':>9}  result")
    for sc in scenes:
        o = OLED(font)
        draw_scene(o, sc, args.time, args.twelve, args.ampm, args.temp,
                   icon, degree)
        canvases.append(o)
        errs = check_scene(sc)
        failed |= bool(errs)
        wx_rows = f"{sc.wx_top}..{sc.wx_top + WX_ICON_H - 1}" if sc.wx else "-"
        print(f"{sc.label:<26} {sc.date or '(hidden)':<13} {sc.dsize:>2} "
              f"{sc.clk_y:>5} {wx_rows:>9}  "
              f"{'OK' if not errs else 'FAIL: ' + '; '.join(errs)}")

    warns = firmware_drift_warnings()
    for w in warns:
        print(f"warning: {w}", file=sys.stderr)

    write_png(args.out, canvases, max(1, args.scale), args.gap)
    print(f"\nwrote {args.out}  ({len(canvases)} panel(s), "
          f"{args.scale}x, {args.time}{' ' + args.ampm if args.twelve else ''}"
          f"{', weather ' + args.icon if any(s.wx for s in scenes) else ''})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
