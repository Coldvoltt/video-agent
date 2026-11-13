"""
Caption and text fonts bundled in editor/fonts (all from github.com/google/fonts; OFL or Apache-2.0,
licences alongside). `ass` is the name libass matches; `em` is unitsPerEm / (winAscent + winDescent):
libass sizes text by the full line height, so the same \\fs draws fonts with a small `em` smaller. Sizes
are scaled by these ratios so every font comes out about as big as the style intends, and the browser
preview (which sizes by the em box) uses the same numbers.
"""

FONTS: dict[str, dict] = {
    "montserrat":       {"ass": "Montserrat ExtraBold", "label": "Montserrat", "em": 0.640,
                         "feel": "clean, bold, modern (the default)"},
    "anton":            {"ass": "Anton", "label": "Anton", "em": 0.577,
                         "feel": "tall and condensed, punchy (the pop style's font)"},
    "bebas":            {"ass": "Bebas Neue", "label": "Bebas Neue", "em": 0.769,
                         "feel": "condensed capitals, cinematic"},
    "archivo_black":    {"ass": "Archivo Black", "label": "Archivo Black", "em": 0.742,
                         "feel": "heavy and wide, loud"},
    "poppins":          {"ass": "Poppins ExtraBold", "label": "Poppins", "em": 0.568,
                         "feel": "rounded and friendly"},
    "bangers":          {"ass": "Bangers", "label": "Bangers", "em": 0.569,
                         "feel": "comic book, energetic"},
    "luckiest_guy":     {"ass": "Luckiest Guy", "label": "Luckiest Guy", "em": 0.816,
                         "feel": "chunky and bubbly, YouTube style"},
    "permanent_marker": {"ass": "Permanent Marker", "label": "Permanent Marker", "em": 0.701,
                         "feel": "hand-drawn marker"},
    "pacifico":         {"ass": "Pacifico", "label": "Pacifico", "em": 0.517,
                         "feel": "retro script, warm"},
    "dm_serif":         {"ass": "DM Serif Display Italic", "label": "DM Serif Italic", "em": 0.729,
                         "feel": "elegant italic serif"},
}
FONT_IDS = tuple(FONTS)


def ass_name(font_id: str) -> str:
    return FONTS[font_id]["ass"]


def em(font_id: str) -> float:
    return FONTS[font_id]["em"]


def describe() -> str:
    """One line per font, for the agent's tool descriptions."""
    return "; ".join(f"{k} ({v['feel']})" for k, v in FONTS.items())
