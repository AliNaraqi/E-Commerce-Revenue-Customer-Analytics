"""Shared chart theme — the dataviz reference palette, light mode.

PNGs cannot respond to a viewer's theme, so this module commits to the light
surface and paints every colour explicitly. Only categorical slots 1-3 are
exposed: those three are the documented all-pairs-safe subset, and no chart in
this project needs a fourth series (anything wider folds into "Other").
"""
from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt

# categorical slots (fixed order — never cycled, never reassigned by rank)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
BLUE, ORANGE, AQUA = SERIES

# sequential blue ramp, light -> dark
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
       "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

# status — reserved, always shipped with a label, never used as "series 4"
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"

SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"


def apply_theme() -> None:
    mpl.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "savefig.dpi": 160,
        "savefig.bbox": "tight",
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 10,
        "text.color": INK,
        "axes.titlesize": 13,
        "axes.titleweight": "600",
        "axes.titlecolor": INK,
        "axes.titlelocation": "left",
        "axes.titlepad": 14,
        "axes.labelsize": 10,
        "axes.labelcolor": INK_2,
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelcolor": INK_2,
        "ytick.labelcolor": INK_2,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "legend.frameon": False,
        "legend.fontsize": 9,
        "legend.labelcolor": INK_2,
        "lines.linewidth": 2.0,
        "lines.markersize": 5,
    })


def titles(ax, title: str, sub: str | None = None, size: float = 13) -> None:
    """Set a left-aligned title with an optional subtitle beneath it.

    Matplotlib has no subtitle slot, so the title is padded upward and the
    subtitle is annotated into the gap. Both are set here in one call: an
    earlier two-call version silently erased the title, because rcParams sets
    `axes.titlelocation = left` while `ax.get_title()` reads the *center* slot.
    """
    pad = 30 if sub else 14
    ax.set_title(title, loc="left", pad=pad, fontsize=size)
    if sub:
        ax.annotate(sub, xy=(0, 1), xycoords="axes fraction", xytext=(0, 7),
                    textcoords="offset points", ha="left", va="bottom",
                    fontsize=9.5, color=INK_2)


def source_note(fig, text: str) -> None:
    fig.text(0.0, -0.02, text, ha="left", va="top", fontsize=8, color=MUTED)


def brl(v: float) -> str:
    """Compact Brazilian-real label."""
    if abs(v) >= 1_000_000:
        return f"R${v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"R${v / 1_000:.0f}k"
    return f"R${v:.0f}"


def hgrid_only(ax) -> None:
    ax.grid(axis="y", visible=True)
    ax.grid(axis="x", visible=False)


def vgrid_only(ax) -> None:
    ax.grid(axis="x", visible=True)
    ax.grid(axis="y", visible=False)
