"""Reading Office Open XML packages (.docx / .pptx / .xlsx) with the standard library only."""

from __future__ import annotations

import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from .typeset import hex_color

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "dsp": "http://schemas.microsoft.com/office/drawing/2008/diagram",
}


def q(prefix_tag: str) -> str:
    prefix, tag = prefix_tag.split(":")
    return "{%s}%s" % (NS[prefix], tag)


class PackageError(Exception):
    pass


class Package:
    """An OPC zip: parts by name, relationships by part."""

    def __init__(self, data: bytes):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise PackageError("ファイルを開けませんでした（Office の新しい形式 .docx / .pptx / .xlsx のみ対応しています）") from exc
        self.names = set(self.zip.namelist())
        self._xml: dict[str, ET.Element | None] = {}
        self._rels: dict[str, dict[str, tuple[str, str, str]]] = {}

    def has(self, name: str) -> bool:
        return name in self.names

    def read(self, name: str) -> bytes | None:
        name = name.lstrip("/")
        if name not in self.names:
            # zip entry names are case-sensitive but producers differ
            lowered = {n.lower(): n for n in self.names}
            name = lowered.get(name.lower(), name)
            if name not in self.names:
                return None
        return self.zip.read(name)

    def xml(self, name: str) -> ET.Element | None:
        name = name.lstrip("/")
        if name not in self._xml:
            raw = self.read(name)
            root = None
            if raw is not None:
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    root = None
            self._xml[name] = root
        return self._xml[name]

    def rels(self, part: str) -> dict[str, tuple[str, str, str]]:
        """id -> (resolved target path or URL, type suffix, mode)."""
        part = part.lstrip("/")
        if part in self._rels:
            return self._rels[part]
        folder, base = posixpath.split(part)
        rels_name = posixpath.join(folder, "_rels", base + ".rels")
        out: dict[str, tuple[str, str, str]] = {}
        root = self.xml(rels_name)
        if root is not None:
            for rel in root:
                target = rel.get("Target") or ""
                mode = rel.get("TargetMode") or "Internal"
                kind = (rel.get("Type") or "").rsplit("/", 1)[-1]
                if mode != "External":
                    target = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
                out[rel.get("Id") or ""] = (target, kind, mode)
        self._rels[part] = out
        return out

    def related(self, part: str, kind: str) -> list[str]:
        return [t for t, k, _m in self.rels(part).values() if k == kind]


# ------------------------------------------------------------------ units

EMU_PER_PT = 12700.0


def emu(value) -> float:
    try:
        return float(value) / EMU_PER_PT
    except (TypeError, ValueError):
        return 0.0


def twips(value) -> float:
    try:
        return float(value) / 20.0
    except (TypeError, ValueError):
        return 0.0


def attr(el: ET.Element | None, name: str, prefix: str = "w", default=None):
    if el is None:
        return default
    return el.get("{%s}%s" % (NS[prefix], name), default)


def on(el: ET.Element | None, prefix: str = "w") -> bool:
    """A boolean toggle element such as <w:b/> or <w:b w:val="0"/>."""
    if el is None:
        return False
    value = el.get("{%s}val" % NS[prefix])
    return value not in ("0", "false", "off", "none")


# ------------------------------------------------------------------ theme

DEFAULT_THEME_COLORS = {
    "dk1": "000000", "lt1": "FFFFFF", "dk2": "44546A", "lt2": "E7E6E6", "accent1": "4472C4", "accent2": "ED7D31",
    "accent3": "A5A5A5", "accent4": "FFC000", "accent5": "5B9BD5", "accent6": "70AD47", "hlink": "0563C1", "folHlink": "954F72",
}
THEME_ALIASES = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2", "dark1": "dk1", "light1": "lt1", "dark2": "dk2", "light2": "lt2",
                 "text1": "dk1", "background1": "lt1", "text2": "dk2", "background2": "lt2"}


class Theme:
    def __init__(self, root: ET.Element | None):
        self.colors = dict(DEFAULT_THEME_COLORS)
        self.fonts = {"major": {"latin": "Calibri Light", "ea": "", "cs": ""}, "minor": {"latin": "Calibri", "ea": "", "cs": ""}}
        if root is None:
            return
        scheme = root.find(".//a:clrScheme", NS)
        if scheme is not None:
            for child in scheme:
                tag = child.tag.rsplit("}", 1)[-1]
                inner = child[0] if len(child) else None
                if inner is None:
                    continue
                value = inner.get("val") if inner.tag.endswith("srgbClr") else inner.get("lastClr")
                if value:
                    self.colors[tag] = value
        for group, key in (("majorFont", "major"), ("minorFont", "minor")):
            fonts = root.find(".//a:%s" % group, NS)
            if fonts is None:
                continue
            for slot, tag in (("latin", "latin"), ("ea", "ea"), ("cs", "cs")):
                node = fonts.find("a:%s" % tag, NS)
                if node is not None and node.get("typeface"):
                    self.fonts[key][slot] = node.get("typeface")
            if not self.fonts[key]["ea"]:
                for font in fonts.findall("a:font", NS):
                    if font.get("script") == "Jpan":
                        self.fonts[key]["ea"] = font.get("typeface") or ""

    def color(self, name: str, tint=None, shade=None, mods: list[tuple[str, float]] | None = None):
        name = THEME_ALIASES.get(name, name)
        base = hex_color(self.colors.get(name, "000000"), (0.0, 0.0, 0.0))
        return apply_mods(base, tint, shade, mods)


def apply_mods(rgb, tint=None, shade=None, mods=None):
    """Apply w:themeTint/themeShade (hex 00-FF) or DrawingML lumMod/lumOff/tint/shade (1/1000 %)."""
    r, g, b = rgb
    if shade not in (None, ""):
        f = int(shade, 16) / 255.0 if isinstance(shade, str) else float(shade)
        r, g, b = r * f, g * f, b * f
    if tint not in (None, ""):
        f = int(tint, 16) / 255.0 if isinstance(tint, str) else float(tint)
        r, g, b = r * f + (1 - f), g * f + (1 - f), b * f + (1 - f)
    for kind, value in mods or []:
        if kind in ("lumMod", "lumOff"):
            h, l, s = _rgb_to_hls(r, g, b)
            if kind == "lumMod":
                l = min(1.0, max(0.0, l * value))
            else:
                l = min(1.0, max(0.0, l + value))
            r, g, b = _hls_to_rgb(h, l, s)
        elif kind == "tint":
            r, g, b = r * value + (1 - value), g * value + (1 - value), b * value + (1 - value)
        elif kind == "shade":
            r, g, b = r * value, g * value, b * value
        elif kind == "satMod":
            h, l, s = _rgb_to_hls(r, g, b)
            r, g, b = _hls_to_rgb(h, l, min(1.0, s * value))
    return (min(1, max(0, r)), min(1, max(0, g)), min(1, max(0, b)))


def _rgb_to_hls(r, g, b):
    import colorsys
    return colorsys.rgb_to_hls(r, g, b)


def _hls_to_rgb(h, l, s):
    import colorsys
    return colorsys.hls_to_rgb(h, l, s)


def color_from_drawingml(el: ET.Element | None, theme: Theme, scheme_map: dict | None = None, default=None):
    """Colour from an <a:solidFill>-style child (srgbClr, schemeClr, sysClr, prstClr...)."""
    if el is None:
        return default
    node = el
    if node.tag.rsplit("}", 1)[-1] in ("solidFill", "fgClr", "bgClr", "clrFrom"):
        node = node[0] if len(node) else None
        if node is None:
            return default
    tag = node.tag.rsplit("}", 1)[-1]
    mods = []
    for child in node:
        ctag = child.tag.rsplit("}", 1)[-1]
        val = child.get("val")
        if ctag in ("lumMod", "lumOff", "tint", "shade", "satMod") and val is not None:
            try:
                mods.append((ctag, int(val) / 100000.0))
            except ValueError:
                pass
    if tag == "srgbClr":
        base = hex_color(node.get("val"), None)
    elif tag == "sysClr":
        base = hex_color(node.get("lastClr") or "000000", None)
    elif tag == "schemeClr":
        name = node.get("val") or "tx1"
        if scheme_map:
            name = scheme_map.get(name, name)
        if name == "phClr":
            return default
        return theme.color(name, mods=mods)
    elif tag == "prstClr":
        base = PRESET_COLORS.get(node.get("val") or "", (0.0, 0.0, 0.0))
    else:
        return default
    if base is None:
        return default
    return apply_mods(base, mods=mods)


PRESET_COLORS = {
    "black": (0, 0, 0), "white": (1, 1, 1), "red": (1, 0, 0), "green": (0, 0.5, 0), "blue": (0, 0, 1), "yellow": (1, 1, 0),
    "gray": (0.5, 0.5, 0.5), "grey": (0.5, 0.5, 0.5), "ltGray": (0.83, 0.83, 0.83), "dkGray": (0.66, 0.66, 0.66),
    "orange": (1, 0.65, 0), "purple": (0.5, 0, 0.5), "cyan": (0, 1, 1), "magenta": (1, 0, 1), "lime": (0, 1, 0),
    "navy": (0, 0, 0.5), "teal": (0, 0.5, 0.5), "maroon": (0.5, 0, 0), "olive": (0.5, 0.5, 0), "silver": (0.75, 0.75, 0.75),
}

WORD_HIGHLIGHTS = {
    "yellow": (1, 1, 0), "green": (0, 1, 0), "cyan": (0, 1, 1), "magenta": (1, 0, 1), "blue": (0, 0, 1), "red": (1, 0, 0),
    "darkBlue": (0, 0, 0.55), "darkCyan": (0, 0.55, 0.55), "darkGreen": (0, 0.39, 0), "darkMagenta": (0.55, 0, 0.55),
    "darkRed": (0.55, 0, 0), "darkYellow": (0.5, 0.5, 0), "darkGray": (0.66, 0.66, 0.66), "lightGray": (0.83, 0.83, 0.83),
    "black": (0, 0, 0), "white": (1, 1, 1),
}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text_of(el: ET.Element) -> str:
    return "".join(t.text or "" for t in el.iter() if local(t.tag) in ("t", "delText"))


_NUM = re.compile(r"-?\d+(\.\d+)?")


def to_roman(n: int, upper=True) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out if upper else out.lower()


def to_letters(n: int, upper=False) -> str:
    out = ""
    n = max(n, 1)
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(97 + rem) + out
    return out.upper() if upper else out


_KANJI = "〇一二三四五六七八九"


def to_kanji(n: int) -> str:
    if n <= 0:
        return "〇"
    if n < 10:
        return _KANJI[n]
    if n < 20:
        return "十" + (_KANJI[n - 10] if n > 10 else "")
    if n < 100:
        tens, ones = divmod(n, 10)
        return _KANJI[tens] + "十" + (_KANJI[ones] if ones else "")
    return str(n)


_AIUEO = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
_IROHA = "いろはにほへとちりぬるをわかよたれそつねならむうゐのおくやまけふこえてあさきゆめみしゑひもせす"
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def format_number(n: int, fmt: str) -> str:
    if fmt in ("decimal", "", None):
        return str(n)
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "lowerLetter":
        return to_letters(n)
    if fmt == "upperLetter":
        return to_letters(n, True)
    if fmt == "lowerRoman":
        return to_roman(n, False)
    if fmt == "upperRoman":
        return to_roman(n, True)
    if fmt in ("decimalEnclosedCircle", "decimalEnclosedCircleChinese"):
        return _CIRCLED[n - 1] if 1 <= n <= 20 else f"({n})"
    if fmt in ("decimalFullWidth", "decimalFullWidth2"):
        return "".join(chr(ord(c) - 48 + 0xFF10) for c in str(n))
    if fmt in ("japaneseCounting", "ideographDigital", "chineseCounting", "chineseCountingThousand", "ideographTraditional", "taiwaneseCounting"):
        if fmt == "ideographDigital":
            return "".join(_KANJI[int(c)] for c in str(n))
        return to_kanji(n)
    if fmt in ("aiueo", "aiueoFullWidth"):
        return _AIUEO[(n - 1) % len(_AIUEO)]
    if fmt in ("iroha", "irohaFullWidth"):
        return _IROHA[(n - 1) % len(_IROHA)]
    if fmt == "ordinal":
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    if fmt == "bullet":
        return "•"
    if fmt == "none":
        return ""
    return str(n)


# Symbol / Wingdings private-use bullets → characters the bundled fonts have.
_BULLET_MAP = {
    "": "•", "": "▪", "": "➢", "": "✓", "": "❖", "": "◻", "": "●",
    "": "■", "": "○", "": "□", "": "→", "": "➔", "": "✗", "": "❑",
    "o": "◦", "§": "▪", "Ø": "➢", "ü": "✓", "v": "❖", "n": "■", "l": "●",
}


def bullet_char(text: str, font: str | None) -> str:
    if not text:
        return "•"
    ch = text[0]
    if ch in ("", "", "", "", "", "", "", "", "", "", "", "", "", ""):
        return _BULLET_MAP[ch]
    if font and ("wingdings" in font.lower() or "symbol" in font.lower()) and ch in _BULLET_MAP:
        return _BULLET_MAP[ch]
    if 0xF000 <= ord(ch) <= 0xF0FF:
        return "•"
    if font and font.lower().startswith("courier") and ch == "o":
        return "◦"
    return text
