"""
Theme definitions for the HandsFree application.

Each Theme is a frozen dataclass of colour tokens used both in the global QSS
and in the few widgets that need programmatic QPainter colours.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name:            str

    # Backgrounds
    bg:              str   # main window / page background
    bg2:             str   # inputs, lists, secondary panels
    bg3:             str   # hover, borders region, incall device strip
    bg_banner:       str   # in-call top banner background

    # Foreground
    fg:              str   # primary text
    fg_dim:          str   # secondary / placeholder / timer text
    fg_banner:       str   # caller number colour inside the banner

    # Structural
    border:          str   # QGroupBox / QLineEdit / QListWidget borders
    tab_bg:          str   # inactive tab background
    tab_selected:    str   # active tab background

    # Interactive elements
    btn_bg:          str
    btn_hover:       str
    btn_pressed:     str

    # Accents
    accent_green:    str   # call / answer button
    accent_blue:     str   # sync icon, links

    # Status bar
    statusbar_fg:    str

    # VU meter dial
    dial_bg:         str   # dial circle background
    dial_track:      str   # inactive arc track


# ── VS Code Dark ──────────────────────────────────────────────────────────────

VSCODE_DARK = Theme(
    name         = "VS Code Dark",
    bg           = "#1c1c1e",
    bg2          = "#2d2d2f",
    bg3          = "#3c4043",
    bg_banner    = "#3a1010",
    fg           = "#e8eaed",
    fg_dim       = "#9aa0a6",
    fg_banner    = "#f28b82",
    border       = "#3c4043",
    tab_bg       = "#2d2d2f",
    tab_selected = "#1c1c1e",
    btn_bg       = "#3c4043",
    btn_hover    = "#4a4d50",
    btn_pressed  = "#5a5d60",
    accent_green = "#34a853",
    accent_blue  = "#8ab4f8",
    statusbar_fg = "#9aa0a6",
    dial_bg      = "#2d2d2f",
    dial_track   = "#3c4043",
)

# ── Dracula ───────────────────────────────────────────────────────────────────

DRACULA = Theme(
    name         = "Dracula",
    bg           = "#282a36",
    bg2          = "#44475a",
    bg3          = "#6272a4",
    bg_banner    = "#44172a",
    fg           = "#f8f8f2",
    fg_dim       = "#6272a4",
    fg_banner    = "#ff5555",
    border       = "#6272a4",
    tab_bg       = "#44475a",
    tab_selected = "#282a36",
    btn_bg       = "#44475a",
    btn_hover    = "#6272a4",
    btn_pressed  = "#bd93f9",
    accent_green = "#50fa7b",
    accent_blue  = "#bd93f9",
    statusbar_fg = "#6272a4",
    dial_bg      = "#44475a",
    dial_track   = "#6272a4",
)

# ── Light ─────────────────────────────────────────────────────────────────────

LIGHT = Theme(
    name         = "Light",
    bg           = "#ffffff",
    bg2          = "#f3f3f3",
    bg3          = "#e8e8e8",
    bg_banner    = "#fce8e6",
    fg           = "#1e1e1e",
    fg_dim       = "#6e7681",
    fg_banner    = "#c5221f",
    border       = "#d1d5da",
    tab_bg       = "#ececec",
    tab_selected = "#ffffff",
    btn_bg       = "#e8e8e8",
    btn_hover    = "#d4d4d4",
    btn_pressed  = "#c0c0c0",
    accent_green = "#1a7f37",
    accent_blue  = "#0969da",
    statusbar_fg = "#6e7681",
    dial_bg      = "#f3f3f3",
    dial_track   = "#d1d5da",
)

# ── Registry ──────────────────────────────────────────────────────────────────

ALL: list[Theme] = [VSCODE_DARK, DRACULA, LIGHT]
BY_NAME: dict[str, Theme] = {t.name: t for t in ALL}


def get(name: str) -> Theme:
    return BY_NAME.get(name, VSCODE_DARK)
