"""Single source of truth for every colour, font and metric in the UI.

Nothing outside this module may contain a ``#RRGGBB`` literal.  The old edition
scattered near-identical greys and blues across seven UI modules, which is why
the product looked assembled from parts.  Every widget, dialog and canvas item
now reads its appearance from here.
"""

from __future__ import annotations

from typing import Union

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------
BG_APP = "#F7F8FA"
BG_PANEL = "#FFFFFF"
BG_SUBTLE = "#F1F3F6"
BG_HOVER = "#EDEFF3"

# Hairlines.  Grouping is done with whitespace, not nested frames, so these are
# used sparingly.
BORDER = "#E3E7ED"
BORDER_STRONG = "#CBD2DB"

# Text
TEXT = "#10151B"
TEXT_SECOND = "#5A6672"
TEXT_MUTED = "#8A94A0"
TEXT_ON_ACCENT = "#FFFFFF"

# Accent
ACCENT = "#2F6FED"
ACCENT_HOVER = "#2158CC"
ACCENT_PRESS = "#1A47A8"
ACCENT_TINT = "#EDF3FF"

# Semantic
SUCCESS = "#12855A"
SUCCESS_TINT = "#E7F6EF"
WARNING = "#B45309"
WARNING_TINT = "#FEF3E2"
DANGER = "#CE3B30"
DANGER_TINT = "#FDEDEB"
DISABLED = "#AEB6C0"


# ---------------------------------------------------------------------------
# Series palette
# ---------------------------------------------------------------------------
# Okabe-Ito derived: safe for the common colour-vision deficiencies and legible
# in monochrome print.  Okabe-Ito's yellow (#F0E442) is dropped because it
# disappears on a white plot background.
SERIES_PALETTE = (
    "#0072B2",  # blue
    "#D55E00",  # vermilion
    "#009E73",  # green
    "#CC79A7",  # rose purple
    "#E69F00",  # orange
    "#56B4E9",  # sky
    "#6E4B9E",  # violet
    "#8C6D1F",  # gold brown
    "#47606E",  # slate
    "#262626",  # ink
)


def series_color(index: int) -> str:
    """Return the palette entry for the ``index``-th series in a mode."""
    return SERIES_PALETTE[index % len(SERIES_PALETTE)]


# ---------------------------------------------------------------------------
# Plot surface
# ---------------------------------------------------------------------------
PLOT_BG = "#FFFFFF"
PLOT_FRAME = "#10151B"
PLOT_GRID = "#E3E7ED"
PLOT_GRID_MINOR = "#F1F3F6"
PLOT_TICK_TEXT = "#5A6672"
PLOT_AXIS_TITLE = "#10151B"
PLOT_LEGEND_BG = "#FFFFFF"
PLOT_LEGEND_BORDER = "#E3E7ED"
PLOT_PLACEHOLDER = "#8A94A0"

# Analysis overlays.  Bands sit behind the curves, so these are pale tints
# rather than saturated fills.
OVERLAY_PRE = "#DCE7FB"
OVERLAY_SEARCH = "#DFF3E8"
OVERLAY_POST = "#F6E6D5"
OVERLAY_AREA = "#F7CFC9"
OVERLAY_BASELINE = "#2F6FED"
OVERLAY_MELT = "#B4451F"
OVERLAY_SMOOTH = "#6E4B9E"
OVERLAY_SELECT = "#0F7A66"
OVERLAY_SELECT_ALT = "#B4451F"
OVERLAY_MARKER = "#0F7A66"
OVERLAY_READOUT = "#6E4B9E"
OVERLAY_TG_ONSET = "#1C4E8A"
OVERLAY_TG_MID = "#0F7A66"
OVERLAY_TG_INFLECTION = "#6E4B9E"
SELECTION_BAND_COLORS = (OVERLAY_PRE, OVERLAY_POST, OVERLAY_SEARCH)


# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
_FAMILY_CANDIDATES = ("Yu Gothic UI", "Meiryo UI", "Segoe UI", "MS UI Gothic")
_MONO_CANDIDATES = ("Consolas", "Cascadia Mono", "Courier New")

FONT_FAMILY = _FAMILY_CANDIDATES[0]
FONT_MONO = _MONO_CANDIDATES[0]

FONT_TITLE = (FONT_FAMILY, 16, "bold")
FONT_HEAD = (FONT_FAMILY, 11, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_LABEL = (FONT_FAMILY, 9)
FONT_NUM = (FONT_MONO, 10)


def resolve_fonts(root: tk.Misc) -> None:
    """Pick the first installed family from each fallback chain.

    Must run after a ``Tk`` root exists because ``families()`` needs a display.
    """
    global FONT_FAMILY, FONT_MONO
    global FONT_TITLE, FONT_HEAD, FONT_BODY, FONT_LABEL, FONT_NUM

    available = set(tkfont.families(root))
    FONT_FAMILY = _first_available(_FAMILY_CANDIDATES, available, "TkDefaultFont")
    FONT_MONO = _first_available(_MONO_CANDIDATES, available, "TkFixedFont")

    FONT_TITLE = (FONT_FAMILY, 16, "bold")
    FONT_HEAD = (FONT_FAMILY, 11, "bold")
    FONT_BODY = (FONT_FAMILY, 10)
    FONT_LABEL = (FONT_FAMILY, 9)
    FONT_NUM = (FONT_MONO, 10)


def _first_available(candidates, available, fallback: str) -> str:
    for name in candidates:
        if name in available:
            return name
    return fallback


# ---------------------------------------------------------------------------
# Spacing — 8 pt grid
# ---------------------------------------------------------------------------
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24
SPACE_XXL = 32

#: Never put more than this many input widgets on one row (see D8/D9).
MAX_FIELDS_PER_ROW = 4

LEFT_RAIL_WIDTH = 300
INSPECTOR_WIDTH = 400


# ---------------------------------------------------------------------------
# High-DPI
# ---------------------------------------------------------------------------
_scale = 1.0


def enable_dpi_awareness() -> None:
    """Opt into per-monitor DPI scaling.  Must run before ``Tk()`` is created."""
    try:
        import ctypes
    except Exception:  # pragma: no cover - non-Windows
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Win8.1+
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Win7 fallback
        except Exception:
            pass


def apply_scaling(root: tk.Misc) -> float:
    """Match Tk's point scaling to the real screen DPI and remember the factor."""
    global _scale
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:  # pragma: no cover - headless
        dpi = 96.0
    if dpi <= 0:
        dpi = 96.0
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:  # pragma: no cover - headless
        pass
    _scale = dpi / 96.0
    return _scale


def scale(value: float) -> float:
    """Convert a logical pixel measurement into device pixels."""
    return value * _scale


def scale_int(value: float) -> int:
    return int(round(value * _scale))


def current_scale() -> float:
    return _scale


# ---------------------------------------------------------------------------
# ttk styles
# ---------------------------------------------------------------------------
def configure_styles(root: tk.Misc) -> ttk.Style:
    """Style every ttk class the app uses.

    Any class left unstyled falls back to the platform theme and shows up as a
    raw grey rectangle, so the list below is deliberately exhaustive.
    """
    style = ttk.Style(root)
    style.theme_use("clam")

    root.option_add("*Font", FONT_BODY)
    root.option_add("*TCombobox*Listbox.font", FONT_BODY)
    root.option_add("*TCombobox*Listbox.background", BG_PANEL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT_ON_ACCENT)

    # --- containers -------------------------------------------------------
    style.configure("TFrame", background=BG_APP)
    style.configure("App.TFrame", background=BG_APP)
    style.configure("Panel.TFrame", background=BG_PANEL)
    style.configure("Subtle.TFrame", background=BG_SUBTLE)
    style.configure("Bar.TFrame", background=BG_PANEL)
    style.configure(
        "Divider.TFrame", background=BORDER, borderwidth=0, relief="flat"
    )

    # --- labels -----------------------------------------------------------
    style.configure("TLabel", background=BG_APP, foreground=TEXT, font=FONT_BODY)
    style.configure("Panel.TLabel", background=BG_PANEL, foreground=TEXT, font=FONT_BODY)
    style.configure("Title.TLabel", background=BG_PANEL, foreground=TEXT, font=FONT_TITLE)
    style.configure("Head.TLabel", background=BG_APP, foreground=TEXT, font=FONT_HEAD)
    style.configure(
        "PanelHead.TLabel", background=BG_PANEL, foreground=TEXT, font=FONT_HEAD
    )
    style.configure(
        "Second.TLabel", background=BG_APP, foreground=TEXT_SECOND, font=FONT_LABEL
    )
    style.configure(
        "PanelSecond.TLabel",
        background=BG_PANEL,
        foreground=TEXT_SECOND,
        font=FONT_LABEL,
    )
    style.configure(
        "Muted.TLabel", background=BG_APP, foreground=TEXT_MUTED, font=FONT_LABEL
    )
    style.configure(
        "Num.TLabel", background=BG_APP, foreground=TEXT, font=FONT_NUM
    )
    style.configure(
        "Error.TLabel", background=BG_APP, foreground=DANGER, font=FONT_LABEL
    )
    style.configure(
        "PanelError.TLabel", background=BG_PANEL, foreground=DANGER, font=FONT_LABEL
    )
    style.configure(
        "Success.TLabel", background=BG_APP, foreground=SUCCESS, font=FONT_LABEL
    )
    style.configure(
        "Warning.TLabel", background=BG_APP, foreground=WARNING, font=FONT_LABEL
    )
    style.configure(
        "Status.TLabel", background=BG_PANEL, foreground=TEXT_SECOND, font=FONT_LABEL
    )
    style.configure(
        "StatusSuccess.TLabel", background=BG_PANEL, foreground=SUCCESS, font=FONT_LABEL
    )
    style.configure(
        "StatusWarning.TLabel", background=BG_PANEL, foreground=WARNING, font=FONT_LABEL
    )
    style.configure(
        "StatusError.TLabel", background=BG_PANEL, foreground=DANGER, font=FONT_LABEL
    )

    # --- buttons ----------------------------------------------------------
    style.configure(
        "TButton",
        background=BG_PANEL,
        foreground=TEXT,
        bordercolor=BORDER_STRONG,
        lightcolor=BG_PANEL,
        darkcolor=BG_PANEL,
        focuscolor=ACCENT_TINT,
        borderwidth=1,
        relief="solid",
        padding=(SPACE_M, SPACE_XS + 2),
        font=FONT_BODY,
    )
    style.map(
        "TButton",
        background=[("disabled", BG_SUBTLE), ("pressed", BG_HOVER), ("active", BG_HOVER)],
        foreground=[("disabled", DISABLED)],
        bordercolor=[("disabled", BORDER), ("active", BORDER_STRONG)],
    )
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground=TEXT_ON_ACCENT,
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        borderwidth=1,
        relief="solid",
        padding=(SPACE_L, SPACE_XS + 2),
        font=FONT_BODY,
    )
    style.map(
        "Accent.TButton",
        background=[
            ("disabled", DISABLED),
            ("pressed", ACCENT_PRESS),
            ("active", ACCENT_HOVER),
        ],
        bordercolor=[
            ("disabled", DISABLED),
            ("pressed", ACCENT_PRESS),
            ("active", ACCENT_HOVER),
        ],
        foreground=[("disabled", TEXT_ON_ACCENT)],
    )
    style.configure(
        "Danger.TButton",
        background=BG_PANEL,
        foreground=DANGER,
        bordercolor=BORDER_STRONG,
        lightcolor=BG_PANEL,
        darkcolor=BG_PANEL,
        borderwidth=1,
        relief="solid",
        padding=(SPACE_M, SPACE_XS + 2),
    )
    style.map(
        "Danger.TButton",
        background=[("disabled", BG_SUBTLE), ("active", DANGER_TINT)],
        foreground=[("disabled", DISABLED)],
    )
    style.configure(
        "Ghost.TButton",
        background=BG_PANEL,
        foreground=TEXT_SECOND,
        bordercolor=BG_PANEL,
        lightcolor=BG_PANEL,
        darkcolor=BG_PANEL,
        borderwidth=0,
        relief="flat",
        padding=(SPACE_S, SPACE_XS),
        font=FONT_LABEL,
    )
    style.map(
        "Ghost.TButton",
        background=[("disabled", BG_PANEL), ("active", BG_HOVER)],
        foreground=[("disabled", DISABLED), ("active", TEXT)],
    )
    style.configure(
        "Rail.TButton",
        background=BG_APP,
        foreground=TEXT_SECOND,
        bordercolor=BG_APP,
        lightcolor=BG_APP,
        darkcolor=BG_APP,
        borderwidth=0,
        relief="flat",
        padding=(SPACE_XS, SPACE_XS),
        font=FONT_LABEL,
    )
    style.map(
        "Rail.TButton",
        background=[("active", BG_HOVER)],
        foreground=[("active", TEXT)],
    )

    # --- entries ----------------------------------------------------------
    for name, surround in (("TEntry", BG_APP), ("Panel.TEntry", BG_PANEL)):
        style.configure(
            name,
            fieldbackground=BG_SUBTLE,
            background=surround,
            foreground=TEXT,
            bordercolor=BORDER_STRONG,
            lightcolor=BORDER_STRONG,
            darkcolor=BORDER_STRONG,
            insertcolor=TEXT,
            borderwidth=1,
            relief="flat",
            padding=(SPACE_S - 2, SPACE_XS),
        )
        style.map(
            name,
            fieldbackground=[("disabled", BG_SUBTLE), ("readonly", BG_SUBTLE)],
            foreground=[("disabled", DISABLED)],
            bordercolor=[("focus", ACCENT), ("invalid", DANGER)],
            lightcolor=[("focus", ACCENT), ("invalid", DANGER)],
            darkcolor=[("focus", ACCENT), ("invalid", DANGER)],
        )
    style.configure("Num.TEntry", font=FONT_NUM)
    style.map("Num.TEntry", bordercolor=[("focus", ACCENT), ("invalid", DANGER)])
    style.configure("Invalid.TEntry", bordercolor=DANGER, lightcolor=DANGER, darkcolor=DANGER)

    # --- combobox ---------------------------------------------------------
    style.configure(
        "TCombobox",
        fieldbackground=BG_SUBTLE,
        background=BG_SUBTLE,
        foreground=TEXT,
        arrowcolor=TEXT_SECOND,
        bordercolor=BORDER_STRONG,
        lightcolor=BORDER_STRONG,
        darkcolor=BORDER_STRONG,
        borderwidth=1,
        relief="flat",
        padding=(SPACE_S - 2, SPACE_XS),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", BG_SUBTLE), ("disabled", BG_SUBTLE)],
        foreground=[("disabled", DISABLED)],
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
        darkcolor=[("focus", ACCENT)],
        arrowcolor=[("disabled", DISABLED)],
    )
    style.configure(
        "Mode.TCombobox",
        fieldbackground=BG_PANEL,
        background=BG_PANEL,
        font=FONT_HEAD,
        padding=(SPACE_S, SPACE_XS + 1),
    )
    style.map(
        "Mode.TCombobox",
        fieldbackground=[("readonly", BG_PANEL), ("disabled", BG_SUBTLE)],
    )

    # --- spinbox ----------------------------------------------------------
    style.configure(
        "TSpinbox",
        fieldbackground=BG_SUBTLE,
        background=BG_SUBTLE,
        foreground=TEXT,
        arrowcolor=TEXT_SECOND,
        bordercolor=BORDER_STRONG,
        lightcolor=BORDER_STRONG,
        darkcolor=BORDER_STRONG,
        borderwidth=1,
        relief="flat",
        padding=(SPACE_XS, SPACE_XS),
    )
    style.map(
        "TSpinbox",
        fieldbackground=[("disabled", BG_SUBTLE)],
        foreground=[("disabled", DISABLED)],
        bordercolor=[("focus", ACCENT)],
    )

    # --- check / radio ----------------------------------------------------
    for cls, surround in (
        ("TCheckbutton", BG_APP),
        ("Panel.TCheckbutton", BG_PANEL),
        ("TRadiobutton", BG_APP),
        ("Panel.TRadiobutton", BG_PANEL),
    ):
        style.configure(
            cls,
            background=surround,
            foreground=TEXT,
            indicatorbackground=BG_PANEL,
            indicatorforeground=ACCENT,
            bordercolor=BORDER_STRONG,
            focuscolor=surround,
            padding=(SPACE_XS, SPACE_XS // 2),
            font=FONT_BODY,
        )
        style.map(
            cls,
            background=[("active", surround)],
            foreground=[("disabled", DISABLED)],
            indicatorbackground=[
                ("disabled", BG_SUBTLE),
                ("selected", ACCENT),
                ("active", BG_HOVER),
            ],
            indicatorforeground=[("selected", TEXT_ON_ACCENT)],
        )

    # --- notebook ---------------------------------------------------------
    style.configure(
        "TNotebook",
        background=BG_APP,
        bordercolor=BORDER,
        borderwidth=0,
        tabmargins=(0, 0, 0, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=BG_APP,
        foreground=TEXT_SECOND,
        bordercolor=BORDER,
        lightcolor=BG_APP,
        darkcolor=BG_APP,
        borderwidth=0,
        padding=(SPACE_M, SPACE_S),
        font=FONT_BODY,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", BG_PANEL), ("active", BG_HOVER)],
        foreground=[("selected", ACCENT), ("disabled", DISABLED)],
        lightcolor=[("selected", BG_PANEL)],
        darkcolor=[("selected", BG_PANEL)],
    )

    # --- treeview ---------------------------------------------------------
    row_height = scale_int(24)
    style.configure(
        "Treeview",
        background=BG_PANEL,
        fieldbackground=BG_PANEL,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BG_PANEL,
        darkcolor=BG_PANEL,
        borderwidth=0,
        relief="flat",
        rowheight=row_height,
        font=FONT_BODY,
    )
    style.map(
        "Treeview",
        background=[("selected", ACCENT_TINT)],
        foreground=[("selected", TEXT)],
    )
    style.configure(
        "Treeview.Heading",
        background=BG_SUBTLE,
        foreground=TEXT_SECOND,
        bordercolor=BORDER,
        lightcolor=BG_SUBTLE,
        darkcolor=BG_SUBTLE,
        borderwidth=0,
        relief="flat",
        padding=(SPACE_S - 2, SPACE_XS + 1),
        font=FONT_LABEL,
    )
    style.map(
        "Treeview.Heading",
        background=[("active", BG_HOVER)],
        relief=[("active", "flat"), ("pressed", "flat")],
    )
    style.layout(
        "Treeview", [("Treeview.treearea", {"sticky": "nswe"})]
    )  # drop the sunken border

    # --- scrollbars -------------------------------------------------------
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            "{0}.TScrollbar".format(orient),
            background=BG_SUBTLE,
            troughcolor=BG_APP,
            bordercolor=BG_APP,
            arrowcolor=TEXT_MUTED,
            lightcolor=BG_SUBTLE,
            darkcolor=BG_SUBTLE,
            borderwidth=0,
            relief="flat",
            arrowsize=scale_int(12),
        )
        style.map(
            "{0}.TScrollbar".format(orient),
            background=[("active", BORDER_STRONG), ("pressed", BORDER_STRONG)],
        )

    # --- panedwindow ------------------------------------------------------
    style.configure("TPanedwindow", background=BG_APP)
    style.configure("TPanedwindow.Sash", sashthickness=scale_int(6))
    style.configure(
        "Sash", background=BG_APP, bordercolor=BORDER, gripcount=0,
        sashthickness=scale_int(6),
    )

    # --- separator / progressbar / labelframe -----------------------------
    style.configure("TSeparator", background=BORDER)
    style.configure(
        "TProgressbar",
        background=ACCENT,
        troughcolor=BG_SUBTLE,
        bordercolor=BG_SUBTLE,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        borderwidth=0,
    )
    style.configure(
        "TLabelframe", background=BG_APP, bordercolor=BORDER, borderwidth=1, relief="flat"
    )
    style.configure(
        "TLabelframe.Label", background=BG_APP, foreground=TEXT_SECOND, font=FONT_LABEL
    )

    return style


def configure_text_widget(widget: tk.Text) -> None:
    """Apply the theme to a classic (non-ttk) ``Text``/``Listbox`` surface."""
    widget.configure(
        background=BG_PANEL,
        foreground=TEXT,
        insertbackground=TEXT,
        selectbackground=ACCENT_TINT,
        selectforeground=TEXT,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        borderwidth=0,
        relief="flat",
        font=FONT_BODY,
    )


def init(root: tk.Misc) -> ttk.Style:
    """One-call theme bootstrap for a freshly created ``Tk`` root."""
    apply_scaling(root)
    resolve_fonts(root)
    style = configure_styles(root)
    try:
        root.configure(background=BG_APP)
    except tk.TclError:  # pragma: no cover - non-window masters
        pass
    return style


def contrast_text_for(color: str) -> str:
    """Return the readable text colour to place on top of ``color``."""
    try:
        red = int(color[1:3], 16)
        green = int(color[3:5], 16)
        blue = int(color[5:7], 16)
    except (ValueError, IndexError):
        return TEXT
    luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0
    return TEXT if luminance > 0.6 else TEXT_ON_ACCENT


def normalize_hex(color: str) -> Union[str, None]:
    """Return ``#RRGGBB`` upper-case, or ``None`` when ``color`` is malformed."""
    text = (color or "").strip()
    if len(text) != 7 or not text.startswith("#"):
        return None
    try:
        int(text[1:], 16)
    except ValueError:
        return None
    return "#" + text[1:].upper()
