"""
Inline image rendering via kitty's `icat` kitten.

Falls back to assets/default.png if a scene has no image or the file is
missing, and no-ops silently if not running inside kitty (or `kitten` isn't
on PATH) so the game still runs fine in other terminals — just text-only.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

ASSETS_DIR = Path(__file__).parent / "assets"
DEFAULT_IMAGE = ASSETS_DIR / "default.png"


def show_image(path: Optional[str], width_cells: int = 40) -> None:
    img_path = Path(path) if path else None
    if img_path is None or not img_path.exists():
        img_path = DEFAULT_IMAGE
    if not img_path.exists():
        return  # no scene image and no default present — skip quietly

    if shutil.which("kitten") is None:
        return  # not running in kitty, or kitten not installed

    subprocess.run(
        ["kitten", "icat", "--align", "left", "--place", f"{width_cells}x20@0x0", str(img_path)],
        check=False,
    )

