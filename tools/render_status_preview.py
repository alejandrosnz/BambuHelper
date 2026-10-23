#!/usr/bin/env python3
"""Pixel preview of the SH1106 printing (incl. pause) and done screens (128x64).

Replays the draw calls of renderPrinting() and renderFinish() in
src/mono_display.cpp with the real FreeMonoBold9pt7b + glcdfont and the real
XBM temp icons, then writes a captioned contact-sheet PNG using the standard
library only (no Pillow needed).

Why this exists: the panel has no antialiasing and hard 128x64 limits, so a
1-px mistake is obvious on hardware but invisible in a diff. Run this before
flashing any layout change and show the PNG to the user for approval.

The layer reuses the idle preview (tools/render_idle_preview.py) for the
classic-font rasterizer and the glcdfont loader, so there is only one copy of
that code. The LAYOUT constants below MIRROR renderPrinting()/renderFinish();
the firmware is grepped on every run and a warning is printed when a constant
drifts from this file.

Usage:
    python tools/render_status_preview.py                  # both sheets
    python tools/render_status_preview.py --screen printing
    python tools/render_status_preview.py --matrix         # every toggle combo
    python tools/render_status_preview.py --scale 6 --cols 2
    python tools/render_status_preview.py --screen done --out-dir preview

Requires the Adafruit GFX library to be installed: run `pio run` once (or
`pio pkg install`) so .pio/libdeps/<env>/.../glcdfont.c and
Fonts/FreeMonoBold9pt7b.h exist.

Sheets written (default run):
    preview/printing.png   curated printing/pause states (every toggle + edge)
    preview/done.png       curated done states (temps on/off, title shapes)
--matrix writes preview/<screen>-matrix.png instead: every boolean OLED
setting combination, for when a toggle interaction is in doubt.

The pause blink is two frames (bar fill drawn / hidden), both in the sheet.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_idle_preview import H, OLED, ROOT, W, load_font  # noqa: E402

# --- LAYOUT: mirrors renderPrinting() ---------------------------------------
TITLE_Y1 = 12
TITLE_Y2 = 27
INFO_Y = 36
BAR_Y = 46
BAR_H = 6
BOTTOM_Y = 55
MAX_LINE = 11          # MONO_MAX_CHAR_TITLE_PER_LINE (mono_display.h)

# --- LAYOUT: mirrors renderFinish() -----------------------------------------
DONE_TITLE_Y1 = 12
DONE_TITLE_Y2 = 25
DONE_BOX_Y = 32
DONE_BOX_H = 18
DONE_BOX_PAD = 20      # boxW = glyph width + 20
DONE_TEXT_DY = 13      # text baseline below boxY
DONE_TEMPS_Y = 54

# --- LAYOUT: mirrors drawTempsRow() -----------------------------------------
TEMP_ICON_W, TEMP_ICON_H = 8, 9
TEMP_X0 = 0
TEMP_GAP_ICON = 10     # x += 10 after the nozzle icon
TEMP_GAP_TEXT = 6      # x += text width + 6 after the nozzle value
TEMP_GAP_BED = 11      # x += 11 after the bed icon

GFX_NAME = "FreeMonoBold9pt7b"


# --- Assets ------------------------------------------------------------------
def load_gfx_font(name: str = GFX_NAME) -> "GfxFont":
    hits = sorted(ROOT.glob(f".pio/libdeps/*/*/Fonts/{name}.h"))
    if not hits:
        sys.exit(f"{name}.h not found: run `pio run` so PlatformIO downloads "
                 "Adafruit GFX.")
    src = hits[0].read_text(encoding="utf-8", errors="ignore")

    bm = re.search(rf"{name}Bitmaps\[\][^=]*=\s*\{{(.*?)\}};", src, re.S)
    gl = re.search(rf"{name}Glyphs\[\][^=]*=\s*\{{(.*?)\}};", src, re.S)
    fn = re.search(rf"GFXfont\s+{name}\s+PROGMEM\s*=\s*\{{.*?,.*?,\s*"
                   rf"(0[xX][0-9a-fA-F]+|\d+)\s*,\s*"
                   rf"(0[xX][0-9a-fA-F]+|\d+)\s*,\s*(\d+)\s*\}}", src, re.S)
    if not (bm and gl and fn):
        sys.exit(f"could not parse {hits[0]}")
    bitmaps = bytes(int(x, 16) for x in re.findall(r"0[xX]([0-9a-fA-F]{2})",
                                                   bm.group(1)))
    glyphs = [tuple(int(v) for v in m) for m in re.findall(
        r"\{\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,"
        r"\s*(-?\d+)\s*,\s*(-?\d+)\s*\}", gl.group(1))]
    first, last, y_adv = (int(fn.group(1), 0), int(fn.group(2), 0),
                          int(fn.group(3)))
    if len(glyphs) != last - first + 1:
        sys.exit(f"glyph table mismatch in {hits[0]}: "
                 f"{len(glyphs)} glyphs for 0x{first:02x}..0x{last:02x}")
    return GfxFont(bitmaps, glyphs, first, last, y_adv)


def load_xbm(path: Path, symbol: str) -> bytes:
    """Read an XBM table out of a C++ source file (NZL_BMP / BED_BMP)."""
    src = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(rf"{symbol}\s*\[\][^=]*=\s*\{{(.*?)\}};", src, re.S)
    if not m:
        sys.exit(f"{symbol} not found in {path}")
    return bytes(int(t, 16) for t in re.findall(r"0[xX]([0-9a-fA-F]{2})",
                                                m.group(1)))


class GfxFont:
    """Just enough of the GFXfont struct: bitmaps + per-glyph metrics."""

    def __init__(self, bitmaps: bytes, glyphs: list[tuple], first: int,
                 last: int, y_advance: int):
        self.bitmaps = bitmaps
        self.glyphs = glyphs
        self.first = first
        self.last = last
        self.y_advance = y_advance

    def glyph(self, ch: int):
        if not (self.first <= ch <= self.last):
            return None
        return self.glyphs[ch - self.first]


# --- GFX emulation -----------------------------------------------------------
class Canvas:
    """The slice of Adafruit_GFX the status screens use, plus draw tracing."""

    def __init__(self, classic: bytes, gfx: GfxFont):
        self.o = OLED(classic)
        self.gfx = gfx
        self.font: GfxFont | None = None
        self.size = 1
        self.cx = 0
        self.cy = 0
        self.oob = 0          # pixels written outside 0..127 / 0..63

    # -- state, same names as the Arduino calls used by the renderer --
    def setFont(self, font: GfxFont | None = None) -> None:
        self.font = font

    def setTextSize(self, size: int) -> None:
        self.size = size

    def setCursor(self, x: int, y: int) -> None:
        self.cx, self.cy = x, y

    def clear(self) -> None:
        self.o.p = [[0] * W for _ in range(H)]
        self.oob = 0

    # -- primitives --
    def _put(self, x: int, y: int) -> None:
        if 0 <= x < W and 0 <= y < H:
            self.o.p[y][x] = 1
        else:
            self.oob += 1

    def drawPixel(self, x: int, y: int) -> None:
        self._put(x, y)

    def drawFastHLine(self, x: int, y: int, w: int) -> None:
        for i in range(w):
            self._put(x + i, y)

    def drawFastVLine(self, x: int, y: int, h: int) -> None:
        for i in range(h):
            self._put(x, y + i)

    def drawLine(self, x0: int, y0: int, x1: int, y1: int) -> None:
        # Adafruit_GFX::drawLine, verbatim (Bresenham, integer halving).
        steep = abs(y1 - y0) > abs(x1 - x0)
        if steep:
            x0, y0 = y0, x0
            x1, y1 = y1, x1
        if x0 > x1:
            x0, x1 = x1, x0
            y0, y1 = y1, y0
        dx, dy = x1 - x0, abs(y1 - y0)
        err = dx // 2
        ystep = 1 if y0 < y1 else -1
        while x0 <= x1:
            if steep:
                self._put(y0, x0)
            else:
                self._put(x0, y0)
            err -= dy
            if err < 0:
                y0 += ystep
                err += dx
            x0 += 1

    def drawRect(self, x: int, y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        self.drawFastHLine(x, y, w)
        self.drawFastHLine(x, y + h - 1, w)
        self.drawFastVLine(x, y, h)
        self.drawFastVLine(x + w - 1, y, h)

    def fillRect(self, x: int, y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        for j in range(h):
            for i in range(w):
                self._put(x + i, y + j)

    def drawBitmap(self, x: int, y: int, data: bytes, w: int, h: int) -> None:
        stride = (w + 7) // 8
        for j in range(h):
            for i in range(w):
                if data[j * stride + i // 8] & (0x80 >> (i % 8)):
                    self._put(x + i, y + j)

    # -- text --
    def print(self, text: str) -> None:
        if not text:
            return
        if self.font is None:
            self.o.text(text, self.cx, self.cy, self.size)
            self.cx += 6 * self.size * len(text)
            return
        size = self.size
        for ch in text:
            g = self.font.glyph(ord(ch) & 0xFF)
            if g is None:
                continue
            off, gw, gh, xa, xo, yo = g
            for yy in range(gh):
                for xx in range(gw):
                    bit = yy * gw + xx
                    if self.font.bitmaps[off + bit // 8] & (0x80 >> (bit % 8)):
                        self._put(self.cx + xo * size + xx * size,
                                  self.cy + yo * size + yy * size)
            self.cx += xa * size

    def getTextBounds(self, text: str, x: int = 0,
                      y: int = 0) -> tuple[int, int, int, int]:
        """Adafruit_GFX::getTextBounds/charBounds with wrap disabled."""
        if not text:
            return (x, y, 0, 0)
        minx, miny, maxx, maxy = 0x7FFF, 0x7FFF, -1, -1
        if self.font is None:
            for _ in text:
                x2, y2 = x + 6 * self.size - 1, y + 8 * self.size - 1
                maxx, maxy = max(maxx, x2), max(maxy, y2)
                minx, miny = min(minx, x), min(miny, y)
                x += 6 * self.size
        else:
            for ch in text:
                g = self.font.glyph(ord(ch) & 0xFF)
                if g is None:
                    continue
                _, gw, gh, xa, xo, yo = g
                x1 = x + xo * self.size
                y1 = y + yo * self.size
                x2 = x1 + gw * self.size - 1
                y2 = y1 + gh * self.size - 1
                minx, miny = min(minx, x1), min(miny, y1)
                maxx, maxy = max(maxx, x2), max(maxy, y2)
                x += xa * self.size
        if maxx >= minx:
            x1, w = minx, maxx - minx + 1
        else:
            x1, w = x, 0
        if maxy >= miny:
            y1, h = miny, maxy - miny + 1
        else:
            y1, h = y, 0
        return (x1, y1, w, h)

    def ink_bbox(self) -> tuple[int, int, int, int] | None:
        xs = [x for y in range(H) for x in range(W) if self.o.p[y][x]]
        ys = [y for y in range(H) if any(self.o.p[y])]
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))


# --- Firmware helpers (mirrored) ---------------------------------------------
def deaccent(text: str) -> str:
    """mono_display.cpp deaccent(): Latin-1 accents to bare ASCII, others drop."""
    out = []
    for ch in text:
        if ord(ch) < 0x80:
            out.append(ch)
        elif ord(ch) <= 0xFF:
            norm = unicodedata.normalize("NFD", ch)
            if norm and ord(norm[0]) < 0x80:
                out.append(norm[0])
    return "".join(out)


def clean_job_name(raw: str) -> str:
    """monoCleanJobName(): slicer suffixes off, '_' to space, deaccent, trim."""
    t = raw
    for suf in (".gcode.3mf", ".3mf", ".gcode"):
        if t.endswith(suf):
            t = t[: -len(suf)]
            break
    return deaccent(t.replace("_", " ")).strip()


def wrap_job_name(raw: str, max_line: int = MAX_LINE) -> tuple[str, str]:
    """wrapJobName2Lines(): greedy first line, '...' on a too-long tail."""
    t = clean_job_name(raw)
    if len(t) <= max_line:
        return t, ""
    last_space = -1
    for i in range(len(t)):
        if i > max_line:
            break
        if t[i] == " ":
            last_space = i
    cut = last_space if last_space > 0 else max_line
    l1 = t[:cut].strip()
    rest = t[cut:].strip()
    if len(rest) > max_line:
        rest = rest[: max_line - 3] + "..."
    return l1, rest


def fmt_temp_val(cur: int, target: int) -> str:
    """fmtTempVal(): '215/220', or '215' when the target is 0."""
    if target < 0.5:
        return f"{cur}"
    return f"{cur}/{target}"


def fmt_remaining_short(minutes: int) -> str:
    """fmtRemainingShort(): '-53m' / '-1h05m' / '--'."""
    if minutes < 0:
        return "--"
    if minutes < 60:
        return f"-{minutes}m"
    return f"-{minutes // 60}h{minutes % 60:02d}m"


def fmt_eta(remaining: int, now_min: int, ntp: bool = True) -> str:
    """fmtETA(): local time + remaining, '+Nd' across midnight, '--:--'."""
    if not ntp or remaining < 0:
        return "--:--"
    end = now_min + remaining
    days = end // (24 * 60)
    mins = end % (24 * 60)
    txt = f"{mins // 60:02d}:{mins % 60:02d}"
    if days > 0:
        txt += f"+{days}"
    return txt


def wrap_stage(stage: str, max_w: int, c: Canvas) -> str:
    """The '...' truncation loop at the bottom of renderPrinting()."""
    if max_w <= 0:
        return ""
    if c.getTextBounds(stage)[2] <= max_w:
        return stage
    cut = len(stage)
    while cut > 0:
        tmp = stage[: min(cut, 44)] + "..."
        if c.getTextBounds(tmp)[2] <= max_w:
            return tmp
        cut -= 1
    return "..."


def draw_progress_bar(c: Canvas, y: int, percent: int, fill_hidden: bool) -> None:
    """monoDrawProgressBar(): 1 px border, map() fill, pause hides the fill."""
    pct = max(0, min(100, percent))
    c.drawRect(0, y, W, BAR_H)
    fill = (pct * 126) // 100          # Arduino map(pct, 0, 100, 0, 126)
    if fill_hidden:
        fill = 0
    c.fillRect(0, y, fill, BAR_H)


def draw_temps_row(c: Canvas, y: int, nzl: int, nzl_t: int, bed: int,
                   bed_t: int) -> int:
    """drawTempsRow(): icon + value + icon + value, returns the right edge."""
    nb = fmt_temp_val(nzl, nzl_t)
    bb = fmt_temp_val(bed, bed_t)
    x = TEMP_X0
    c.drawBitmap(x, y, TEMP_ICONS["NZL_BMP"], TEMP_ICON_W, TEMP_ICON_H)
    x += TEMP_GAP_ICON
    c.setCursor(x, y)
    c.print(nb)
    x += c.getTextBounds(nb)[2] + TEMP_GAP_TEXT
    c.drawBitmap(x, y, TEMP_ICONS["BED_BMP"], TEMP_ICON_W, TEMP_ICON_H)
    x += TEMP_GAP_BED
    c.setCursor(x, y)
    c.print(bb)
    return x + c.getTextBounds(bb)[2]


TEMP_ICONS: dict[str, bytes] = {}


# --- Scenes ------------------------------------------------------------------
@dataclass
class PrintScene:
    """One mocked BambuState + the OLED toggles, for renderPrinting()."""

    label: str
    job: str = "Cube"            # subtaskName (cleaned inside the renderer)
    pct: int = 50                # st.progress
    has_data: bool = True        # st.lastPrintDataMs != 0
    remaining: int = 45          # st.remainingMinutes
    layer: int = 123
    total: int = 456
    stage: str = "Printing"      # stageToText(st)
    paused: bool = False         # MONO_SCR_PAUSE
    blink: bool = False          # pause blink phase that hides the bar fill
    layer_on: bool = True        # monoSettings.showLayer
    bar_on: bool = True          # monoSettings.showBar
    time_on: bool = True         # monoSettings.showTimePrinting
    alt_on: bool = True          # monoSettings.showTimeAlt
    alt_phase: int = 0           # 1 = the remaining-time half of the alternation
    ntp: bool = True             # ntpOk()
    now_min: int = 15 * 60       # local now, minutes since midnight


@dataclass
class DoneScene:
    """One mocked BambuState + the toggles, for renderFinish()."""

    label: str
    job: str = "Cube"
    temps: bool = False          # monoSettings.showTempsDone
    nozzle: int = 40
    nozzle_t: int = 0
    bed: int = 35
    bed_t: int = 0


def render_printing(c: Canvas, sc: PrintScene) -> dict:
    """renderPrinting(), line by line."""
    c.clear()
    pct = sc.pct if sc.has_data else 0
    l1, l2 = wrap_job_name(sc.job)
    c.setFont(c.gfx)
    c.setCursor(0, TITLE_Y1)
    c.print(l1)
    if l2:
        c.setCursor(0, TITLE_Y2)
        c.print(l2)
    c.setFont()
    c.setTextSize(1)

    # Row above the bar: % left | layer/total right
    c.setCursor(0, INFO_Y)
    c.print("--%" if not sc.has_data else f"{pct}%")
    if sc.layer_on:
        layer_txt = ("--/--" if (not sc.has_data or sc.total == 0)
                     else f"{sc.layer}/{sc.total}")
        c.setCursor(W - c.getTextBounds(layer_txt)[2], INFO_Y)
        c.print(layer_txt)

    if sc.bar_on:
        draw_progress_bar(c, BAR_Y, pct, sc.paused and sc.blink)

    # Row below the bar: stage left | remaining/ETA right
    if not sc.time_on:
        right = ""
    elif not sc.has_data:
        right = "--:--"
    elif sc.alt_on and sc.alt_phase == 1:
        right = fmt_remaining_short(sc.remaining)
    else:
        right = fmt_eta(sc.remaining, sc.now_min, sc.ntp)
    eta_w = c.getTextBounds(right)[2]
    eta_x = W - eta_w
    stage = wrap_stage(sc.stage, eta_x - 4, c)
    c.setCursor(0, BOTTOM_Y)
    c.print(stage)
    c.setCursor(eta_x, BOTTOM_Y)
    c.print(right)
    return {"l1": l1, "l2": l2, "right": right, "stage": stage, "pct": pct}


def render_finish(c: Canvas, sc: DoneScene) -> dict:
    """renderFinish(), line by line."""
    c.clear()
    l1, l2 = wrap_job_name(sc.job)
    c.setFont(c.gfx)
    c.setCursor(0, DONE_TITLE_Y1)
    c.print(l1)
    if l2:
        c.setCursor(0, DONE_TITLE_Y2)
        c.print(l2)

    _, _, w, _ = c.getTextBounds("DONE!")
    box_w = w + DONE_BOX_PAD
    box_x = (W - box_w) // 2
    c.drawRect(box_x, DONE_BOX_Y, box_w, DONE_BOX_H)
    c.setCursor((W - w) // 2, DONE_BOX_Y + DONE_TEXT_DY)
    c.print("DONE!")
    c.setFont()
    c.setTextSize(1)

    temps_end = None
    if sc.temps:
        temps_end = draw_temps_row(c, DONE_TEMPS_Y, sc.nozzle, sc.nozzle_t,
                                   sc.bed, sc.bed_t)
    return {"l1": l1, "l2": l2, "box": (box_x, DONE_BOX_Y, box_w, DONE_BOX_H),
            "temps_end": temps_end}


def printing_scenes() -> list[PrintScene]:
    now = 15 * 60
    base = dict(job="Phone_stand_20_chars", pct=50, remaining=83, layer=123,
                total=456)
    return [
        PrintScene("default 50% + ETA", **base),
        PrintScene("start 0% - 1-line title", job="Cube", pct=0, remaining=45,
                   layer=1, total=200, stage="Printing", now_min=now),
        PrintScene("87% - long name (wrap + ...)", pct=87, remaining=125,
                   layer=300, total=345, now_min=now,
                   job="Very_long_part_for_marquee_testing_that_overflows.gcode.3mf"),
        PrintScene("long single word (hard cut)", pct=73, remaining=61,
                   layer=280, total=345, now_min=now,
                   job="Supercalifragilistic_expialidocious"),
        PrintScene("100% - bar full", job="Cube", pct=100, remaining=0,
                   layer=200, total=200, stage="Printing", now_min=now),
        PrintScene("paused - bar fill shown", stage="[Paused]", paused=True,
                   **base),
        PrintScene("paused - blink empty bar", stage="[Paused]", paused=True,
                   blink=True, **base),
        PrintScene("no data (--% --/-- --:--)", has_data=False, job="Cube",
                   stage="Printing", now_min=now),
        PrintScene("layer off", layer_on=False, **base),
        PrintScene("bar off", bar_on=False, **base),
        PrintScene("time off - stage full width", time_on=False,
                   stage="Changing filament", **base),
        PrintScene("stage truncated + ETA", stage="Changing filament", **base),
        PrintScene("remaining instead of ETA", alt_phase=1, **base),
        PrintScene("timeAlt off - always ETA", alt_on=False, alt_phase=1,
                   **base),
        PrintScene("ETA crosses midnight (+1)", now_min=23 * 60 + 40,
                   **base),
        PrintScene("no NTP - ETA --:--", ntp=False, **base),
    ]


def printing_matrix_scenes() -> list[PrintScene]:
    """Every showLayer x showBar x showTimePrinting x showTimeAlt combo."""
    out = []
    for layer in (True, False):
        for bar in (True, False):
            for time_on in (True, False):
                for alt in (True, False):
                    out.append(PrintScene(
                        f"layer={int(layer)} bar={int(bar)} "
                        f"time={int(time_on)} alt={int(alt)}",
                        layer_on=layer, bar_on=bar, time_on=time_on,
                        alt_on=alt, alt_phase=1 if alt else 0))
    return out


def done_scenes() -> list[DoneScene]:
    return [
        DoneScene("default - no temps", job="Cube"),
        DoneScene("temps on - cooldown targets 0", job="Cube", temps=True,
                  nozzle=40, nozzle_t=0, bed=35, bed_t=0),
        DoneScene("temps on - right after finish", job="Cube", temps=True,
                  nozzle=215, nozzle_t=220, bed=60, bed_t=60),
        DoneScene("2-line title + temps", job="Phone_stand_20_chars",
                  temps=True, nozzle=215, nozzle_t=220, bed=60, bed_t=60),
        DoneScene("long name (wrap + ...) + temps",
                  job="Very_long_part_for_marquee_testing_that_overflows.gcode.3mf",
                  temps=True, nozzle=215, nozzle_t=220, bed=60, bed_t=60),
        DoneScene("3-digit temps 300/300", job="Cube", temps=True,
                  nozzle=300, nozzle_t=300, bed=110, bed_t=110),
        DoneScene("non-Latin name - empty title", job="Печать", temps=False),
    ]


def done_matrix_scenes() -> list[DoneScene]:
    titles = [
        ("1-line", "Cube"),
        ("2-line", "Phone_stand_20_chars"),
        ("overflow", "Very_long_part_for_marquee_testing_that_overflows"),
    ]
    out = []
    for temps in (False, True):
        for name, job in titles:
            out.append(DoneScene(f"temps={int(temps)} {name}", job=job,
                                 temps=temps, nozzle=215, nozzle_t=220,
                                 bed=60, bed_t=60))
    return out


# --- Drift checks -------------------------------------------------------------
def firmware_drift_warnings() -> list[str]:
    """Warn if this preview no longer matches mono_display.cpp."""
    src = (ROOT / "src" / "mono_display.cpp").read_text(
        encoding="utf-8", errors="ignore")
    hdr = (ROOT / "src" / "mono_display.h").read_text(
        encoding="utf-8", errors="ignore")
    warns = []

    def grab(pattern: str, expected, name: str, text: str = src) -> None:
        m = re.search(pattern, text)
        if not m:
            warns.append(f"could not find {name} in the firmware")
            return
        found = (tuple(int(g) for g in m.groups()) if len(m.groups()) > 1
                 else int(m.group(1)))
        if found != expected:
            warns.append(f"{name}: firmware {found} vs preview {expected}")

    grab(r"static const int PRINT_TITLE_Y1 = (\d+);", TITLE_Y1, "PRINT_TITLE_Y1")
    grab(r"static const int PRINT_TITLE_Y2 = (\d+);", TITLE_Y2, "PRINT_TITLE_Y2")
    grab(r"static const int PRINT_INFO_Y1 = (\d+);", INFO_Y, "PRINT_INFO_Y1")
    grab(r"static const int PRINT_BAR_Y = (\d+);", BAR_Y, "PRINT_BAR_Y")
    grab(r"static const int PRINT_BOTTOM_Y = (\d+);", BOTTOM_Y, "PRINT_BOTTOM_Y")
    grab(r"static const int BAR_H = (\d+);", BAR_H, "BAR_H")
    grab(r"#define MONO_MAX_CHAR_TITLE_PER_LINE (\d+)", MAX_LINE,
         "MONO_MAX_CHAR_TITLE_PER_LINE", hdr)
    grab(r"const int boxH = (\d+);", DONE_BOX_H, "finish boxH")
    grab(r"const int boxW = \(int\)w \+ (\d+);", DONE_BOX_PAD, "finish boxW pad")
    grab(r"const int boxY = (\d+);", DONE_BOX_Y, "finish boxY")
    grab(r"boxY \+ (\d+)\)", DONE_TEXT_DY, "finish DONE! baseline offset")
    grab(r"drawTempsRow\((\d+), st\.nozzleTemp", DONE_TEMPS_Y,
         "finish temps row y")
    grab(r"drawNozzleIcon\(x, y\);\s*\n\s*x \+= (\d+);", TEMP_GAP_ICON,
         "temps gap after nozzle icon")
    grab(r"x \+= \(int\)w \+ (\d+);", TEMP_GAP_TEXT, "temps gap after value")
    grab(r"drawBedIcon\(x, y\);\s*\n\s*x \+= (\d+);", TEMP_GAP_BED,
         "temps gap after bed icon")

    fin = src[src.find("static void renderFinish("):src.find("static void renderIdle(")]
    found = [int(v) for v in re.findall(r"setCursor\(0, (\d+)\)", fin)]
    if found[:2] != [DONE_TITLE_Y1, DONE_TITLE_Y2]:
        warns.append(f"finish title baselines: firmware {found[:2]} vs preview "
                     f"{[DONE_TITLE_Y1, DONE_TITLE_Y2]}")
    return warns


# --- PNG sheet ----------------------------------------------------------------
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _draw_label(img: list[list[int]], x: int, y: int, text: str,
                font: bytes, px: int) -> None:
    for ch in text:
        base = (ord(ch) & 0xFF) * 5
        for i in range(5):
            col = font[base + i]
            for j in range(8):
                if col & (1 << j):
                    for a in range(px):
                        for b in range(px):
                            yy, xx = y + j * px + a, x + i * px + b
                            if 0 <= yy < len(img) and 0 <= xx < len(img[0]):
                                img[yy][xx] = 0
        x += 6 * px


def _wrap_caption(text: str, max_chars: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def write_sheet(path: Path, panels: list[tuple[str, Canvas]], font: bytes,
                scale: int, cols: int, gap: int) -> None:
    """Grayscale contact sheet, stdlib only, 1 px gray frame around each panel."""
    scale = max(1, scale)
    cap_px = max(2, scale // 2 + 1)
    cell_w = (W + 2) * scale
    max_chars = max(12, (cell_w - 2 * scale) // (6 * cap_px))
    captions = [_wrap_caption(f"{i + 1}. {label}", max_chars)
                for i, (label, _) in enumerate(panels)]
    max_lines = max(len(c) for c in captions)
    cap_line_h = 9 * cap_px
    cap_h = max_lines * cap_line_h + 2 * cap_px
    cell_h = (H + 2) * scale + cap_h
    rows = (len(panels) + cols - 1) // cols
    pad = 3 * scale
    width = 2 * pad + cols * cell_w + (cols - 1) * gap
    height = 2 * pad + rows * cell_h + (rows - 1) * gap
    img = [[255] * width for _ in range(height)]

    for k, (_, canvas) in enumerate(panels):
        col, row = k % cols, k // cols
        ox = pad + col * (cell_w + gap) + scale
        oy = pad + row * (cell_h + gap) + scale
        for y in range(H):
            for x in range(W):
                if canvas.o.p[y][x]:
                    for a in range(scale):
                        for b in range(scale):
                            img[oy + y * scale + a][ox + x * scale + b] = 0
        top, bottom = oy - 1, oy + H * scale
        left, right = ox - 1, ox + W * scale
        for x in range(left, right + 1):
            img[top][x] = 170
            img[bottom][x] = 170
        for y in range(top, bottom + 1):
            img[y][left] = 170
            img[y][right] = 170
        ty = oy + H * scale + scale + 2 * cap_px
        for line in captions[k]:
            _draw_label(img, ox, ty, line, font, cap_px)
            ty += cap_line_h

    raw = b"".join(b"\x00" + bytes(r) for r in img)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" +
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0,
                                        0, 0)) +
        _png_chunk(b"IDAT", zlib.compress(raw, 9)) +
        _png_chunk(b"IEND", b""))


# --- Main ---------------------------------------------------------------------
def run_sheet(kind: str, scenes: list, out: Path, font: bytes, gfx: GfxFont,
              scale: int, cols: int, gap: int) -> bool:
    panels: list[tuple[str, Canvas]] = []
    failed = False
    print(f"\n{kind} ({len(scenes)} panels) -> {out}")
    if kind.startswith("printing"):
        print(f"  {'#':>2}  {'scene':<30} {'line1':<12} {'line2':<12} "
              f"{'bottom-left':<18} {'right':>7}  {'ink bbox':<14} result")
    else:
        print(f"  {'#':>2}  {'scene':<30} {'line1':<12} {'line2':<12} "
              f"{'box x..x+w':<13}  {'ink bbox':<14} result")

    for i, sc in enumerate(scenes):
        c = Canvas(font, gfx)
        if kind.startswith("printing"):
            info = render_printing(c, sc)
            extra = f"{info['stage'][:18]:<18} {info['right']:>7}"
        else:
            info = render_finish(c, sc)
            bx, _, bw, _ = info["box"]
            extra = f"{bx}..{bx + bw - 1:<7}"
        bbox = c.ink_bbox()
        errs = []
        if c.oob:
            errs.append(f"{c.oob} px outside the panel")
        if not bbox:
            errs.append("nothing drawn")
        if kind.startswith("done"):
            bx, _, bw, _ = info["box"]
            if bx < 0 or bx + bw > W:
                errs.append(f"DONE! box {bx}..{bx + bw - 1} leaves the panel")
            if info["temps_end"] is not None and info["temps_end"] > W:
                errs.append(f"temps row ends at x={info['temps_end']}")
        if bbox and (bbox[0] < 0 or bbox[2] >= W or bbox[1] < 0 or bbox[3] >= H):
            errs.append(f"ink bbox {bbox} leaves the panel")
        failed |= bool(errs)
        bb = f"{bbox[0]}..{bbox[2]} {bbox[1]}..{bbox[3]}" if bbox else "-"
        print(f"  {i + 1:>2}  {sc.label:<30} "
              f"{info['l1'][:12]:<12} {info['l2'][:12]:<12} "
              f"{extra:<{27 if kind.startswith('printing') else 13}} "
              f"{bb:<14} {'OK' if not errs else 'FAIL: ' + '; '.join(errs)}")
        panels.append((sc.label, c))

    write_sheet(out, panels, font, scale, cols, gap)
    print(f"\nwrote {out}  ({len(panels)} panels, {scale}x, {cols} column(s))")
    return not failed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Render the SH1106 printing/done screens to captioned PNGs.")
    ap.add_argument("--screen", choices=("printing", "done", "all"),
                    default="all", help="which screen(s) to render (default all)")
    ap.add_argument("--matrix", action="store_true",
                    help="render every boolean OLED setting combination")
    ap.add_argument("--scale", type=int, default=4,
                    help="pixels per OLED px (default 4)")
    ap.add_argument("--cols", type=int, default=0,
                    help="panels per row (default: auto from panel count)")
    ap.add_argument("--gap", type=int, default=24,
                    help="gap between panels in output px (default 24)")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "preview",
                    help="output directory (default preview/)")
    args = ap.parse_args()

    global TEMP_ICONS
    TEMP_ICONS = {
        "NZL_BMP": load_xbm(ROOT / "src" / "mono_display.cpp", "NZL_BMP"),
        "BED_BMP": load_xbm(ROOT / "src" / "mono_display.cpp", "BED_BMP"),
    }
    font = load_font()
    gfx = load_gfx_font()

    jobs = []
    if args.screen in ("printing", "all"):
        scenes = (printing_matrix_scenes() if args.matrix
                  else printing_scenes())
        suffix = "-matrix" if args.matrix else ""
        jobs.append(("printing", scenes, args.out_dir / f"printing{suffix}.png"))
    if args.screen in ("done", "all"):
        scenes = done_matrix_scenes() if args.matrix else done_scenes()
        suffix = "-matrix" if args.matrix else ""
        jobs.append(("done", scenes, args.out_dir / f"done{suffix}.png"))

    for w in firmware_drift_warnings():
        print(f"warning: {w}", file=sys.stderr)

    ok = True
    for kind, scenes, out in jobs:
        n = len(scenes)
        cols = args.cols or (4 if n > 9 else 3 if n > 4 else n)
        ok &= run_sheet(kind, scenes, out, font, gfx, args.scale,
                        max(1, cols), args.gap)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
