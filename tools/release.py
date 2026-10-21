#!/usr/bin/env python3
"""Build and package the OTA asset for a BambuHelper-OLED release.

The portal's online updater looks for a release asset named

    BambuHelper-OLED-<BOARD_VARIANT>-<FW_VERSION>-ota.bin

which is the plain PlatformIO `firmware.bin` (ESP32 OTA images are not merged
with the bootloader/partition table). This script builds the only supported
environment, checks the image against the OTA slot, and writes the correctly
named file under firmware/<version>/.

Usage:
    python tools/release.py
    python tools/release.py --skip-build     # reuse .pio/build/sh1106_esp32c3

Then publish it:

    gh release create v3.9.1 \
        firmware/v3.9.1/BambuHelper-OLED-sh1106_esp32c3-v3.9.1-ota.bin \
        --title "BambuHelper-OLED v3.9.1" --notes "..."

The version is read from FW_VERSION in include/config.h, so bump it there
before tagging.
"""

import argparse
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_H = ROOT / "include" / "config.h"
PARTITIONS = ROOT / "partitions_4mb.csv"
ENV = "sh1106_esp32c3"
ASSET_PREFIX = "BambuHelper-OLED"


def read_version() -> str:
    """Extract FW_VERSION from include/config.h."""
    if not CONFIG_H.exists():
        sys.exit(f"error: {CONFIG_H} not found")
    pattern = re.compile(r'#define\s+FW_VERSION\s+"([^"]+)"')
    for line in CONFIG_H.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    sys.exit("error: FW_VERSION not found in include/config.h")


def app_slot_size() -> int:
    """Return the size of the first app partition that OTA can write."""
    if not PARTITIONS.exists():
        sys.exit(f"error: {PARTITIONS} not found")
    with PARTITIONS.open(newline="", encoding="utf-8") as handle:
        for row in csv.reader(line for line in handle if not line.lstrip().startswith("#")):
            if len(row) < 5:
                continue
            name, kind, subtype, _offset, size = (cell.strip() for cell in row[:5])
            if kind == "app" and (name == "app0" or subtype in ("ota_0", "factory")):
                return int(size, 0)
    sys.exit(f"error: no OTA app partition found in {PARTITIONS.name}")


def find_pio() -> str:
    """Locate pio on PATH, then in the default PlatformIO penv."""
    on_path = shutil.which("pio")
    if on_path:
        return on_path
    home = Path.home()
    candidates = [
        home / ".platformio" / "penv" / "Scripts" / "pio.exe",
        home / ".platformio" / "penv" / "bin" / "pio",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    sys.exit("error: pio not found (install PlatformIO or add it to PATH)")


def build() -> None:
    command = [find_pio(), "run", "-e", ENV]
    print("--- Building ---")
    print("  " + " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f"error: build failed with exit code {result.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Package a BambuHelper-OLED OTA release asset")
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing firmware.bin")
    args = parser.parse_args()

    version = read_version()
    asset_name = f"{ASSET_PREFIX}-{ENV}-{version}-ota.bin"
    firmware = ROOT / ".pio" / "build" / ENV / "firmware.bin"

    print(f"BambuHelper-OLED release: {version}")
    print(f"Asset: {asset_name}")

    if not args.skip_build:
        build()

    if not firmware.is_file():
        sys.exit(f"error: {firmware} not found - run without --skip-build first")

    image_size = firmware.stat().st_size
    slot_size = app_slot_size()
    if image_size > slot_size:
        sys.exit(
            f"error: firmware.bin is {image_size:,} B but the OTA slot is "
            f"{slot_size:,} B - refusing to package an image that cannot update"
        )

    out_dir = ROOT / "firmware" / version
    out_dir.mkdir(parents=True, exist_ok=True)
    asset = out_dir / asset_name
    shutil.copyfile(firmware, asset)

    print(f"\n  Image: {image_size:,} B / {slot_size:,} B slot")
    print(f"  OTA:   {asset.relative_to(ROOT)} ({asset.stat().st_size:,} B)")
    print("\nPublish with:")
    print(
        f'  gh release create {version} "{asset.relative_to(ROOT)}" '
        f'--title "BambuHelper-OLED {version}" --notes "..."'
    )


if __name__ == "__main__":
    main()
