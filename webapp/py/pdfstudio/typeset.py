"""A small typesetter: fonts, text measuring, line breaking and page drawing.

The "to PDF" converters (Word, PowerPoint, Excel) all need the same few
things, and none of the HTML-to-PDF paths in MuPDF are good enough for
Japanese office documents (no underline, no strike-through, no bold for a
single-weight face, no italics). So the converters lay their text out
themselves and draw it with this module.

What is here:

* ``FontBook`` – maps the font names found in office files onto the bundled
  open-licensed faces (BIZ UD Gothic / Mincho), with synthetic bold and italic
  where a face has none, and a fallback face for glyphs a face lacks.
* ``Style`` – the character formatting of a run.
* ``atomize`` / ``break_lines`` / ``place_line`` – turn runs into lines, with
  Japanese line-breaking rules (kinsoku), tabs with leaders, alignment and
  justification.
* ``Sheet`` / ``render`` – a display list per page, drawn into a PDF at the end
  (so headers and footers can know the page count).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pymupdf

# ----------------------------------------------------------------------------- fonts

_FONT_DIRS = [
    "/fonts",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "vendor", "fonts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp", "vendor", "fonts"),
]

FONT_FILES = ("BIZUDPGothic-Regular.ttf", "BIZUDPGothic-Bold.ttf", "BIZUDPMincho-Regular.ttf")

_SERIF_WORDS = (
    "mincho", "明朝", "serif", "times", "century", "cambria", "georgia", "garamond", "palatino",
    "book antiqua", "bookman", "simsun", "mingliu", "song", "ming", "ＭＳ 明朝", "ms 明朝", "hg明朝",
    "hgs明朝", "hgp明朝", "游明朝", "yu mincho", "cmr", "minion", "constantia", "baskerville",
)
# Fonts whose single line is much taller than their em square (Yu Gothic and
# Yu Mincho carry a big line gap; Word's "single" spacing follows it).
_LINE_FACTORS = (
    (("游ゴシック", "yu gothic", "游明朝", "yu mincho", "yugothic", "yumincho"), 1.70),
    (("meiryo", "メイリオ"), 1.50),
    (("ms gothic", "ms mincho", "ｍｓ ゴシック", "ｍｓ 明朝", "ＭＳ ゴシック", "ＭＳ 明朝", "ms ゴシック", "ms 明朝",
      "ms pゴシック", "ms p明朝", "ｍｓ ｐゴシック", "ｍｓ ｐ明朝", "ＭＳ Ｐゴシック", "ＭＳ Ｐ明朝",
      "hg", "ＨＧ"), 1.00),
    (("calibri",), 1.22),
    (("arial", "helvetica", "segoe", "verdana", "tahoma"), 1.15),
    (("times", "cambria", "georgia"), 1.17),
)

_files: dict[str, str | None] = {}
_fonts: dict[str, pymupdf.Font | None] = {}
_font_data: dict[int, bytes] = {}      # id(Font) -> the font file, for embedding


def _find(name: str) -> str | None:
    if name in _files:
        return _files[name]
    found = None
    for folder in _FONT_DIRS:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            found = os.path.abspath(path)
            break
    _files[name] = found
    return found


def _load(name: str) -> pymupdf.Font | None:
    if name not in _fonts:
        path = _find(name)
        try:
            font = None
            if path:
                with open(path, "rb") as handle:
                    raw = handle.read()
                font = pymupdf.Font(fontbuffer=raw)
                _font_data[id(font)] = raw
            _fonts[name] = font
        except Exception:  # noqa: BLE001 - a bad font file must not stop a conversion
            _fonts[name] = None
    return _fonts[name]


_serif_cache: dict = {}
_factor_cache: dict = {}
_SERIF_LOWER = tuple(w.lower() for w in _SERIF_WORDS)


def is_serif(family: str | None) -> bool:
    hit = _serif_cache.get(family)
    if hit is None:
        name = (family or "").lower()
        hit = any(word in name for word in _SERIF_LOWER)
        _serif_cache[family] = hit
    return hit


# PowerPoint keeps Yu Gothic / Yu Mincho much tighter than Word does.
_PPT_FACTORS = ((("游ゴシック", "yu gothic", "游明朝", "yu mincho", "yugothic", "yumincho"), 1.38),)


def line_factor(family: str | None, app: str = "word") -> float:
    key = (family, app)
    hit = _factor_cache.get(key)
    if hit is None:
        hit = _factor_cache[key] = _line_factor(family, app)
    return hit


def _line_factor(family: str | None, app: str) -> float:
    name = (family or "").lower()
    if app == "ppt":
        for words, factor in _PPT_FACTORS:
            if any(word.lower() in name for word in words):
                return factor
    for words, factor in _LINE_FACTORS:
        if any(word.lower() in name for word in words):
            return factor
    return 1.22


class Face:
    """One drawable face: a font plus how to fake what it lacks."""

    __slots__ = ("key", "font", "fake_bold", "fake_italic", "adv", "ascent", "descent", "_has", "_gid")

    def __init__(self, key: str, font: pymupdf.Font, fake_bold: bool, fake_italic: bool):
        self.key = key
        self.font = font
        self.fake_bold = fake_bold
        self.fake_italic = fake_italic
        self.adv: dict[str, tuple[float, float, float]] = {}
        self._has: dict[str, bool] = {}
        self._gid: dict[str, int] = {}
        self.ascent = font.ascender or 0.88
        self.descent = -(font.descender or -0.12)

    def has(self, ch: str) -> bool:
        return self.gid(ch) > 0

    def metrics(self, ch: str) -> tuple[float, float, float]:
        """(layout advance, draw shift, natural advance) in em.

        The bundled faces are proportional ("P") designs: kana and punctuation
        are narrower than a full square. Office documents are laid out with
        full-width Japanese characters, so lines only break where Word broke
        them if every such character takes a full em here too; the glyph is
        then shifted inside its square the way a full-width face would draw it.
        """
        hit = self.adv.get(ch)
        if hit is None:
            try:
                natural = self.font.glyph_advance(ord(ch))
            except Exception:  # noqa: BLE001
                natural = 0.5
            layout, shift = natural, 0.0
            if natural < 0.995 and is_cjk(ch) and not (0xFF61 <= ord(ch) <= 0xFF9F) and ord(ch) > 0x2000:
                layout = 1.0
                if ch in _OPENING:
                    shift = 1.0 - natural
                elif ch in _CENTERED or (0xFF01 <= ord(ch) <= 0xFF5E and ch not in "，．"):
                    shift = (1.0 - natural) / 2
            if self.fake_bold:
                layout += 0.02
            hit = (layout, shift, natural)
            self.adv[ch] = hit
        return hit

    def advance(self, ch: str) -> float:
        return self.metrics(ch)[0]

    def natural(self, ch: str) -> float:
        return self.metrics(ch)[2]

    def gid(self, ch: str) -> int:
        hit = self._gid.get(ch)
        if hit is None:
            hit = self._gid[ch] = int(self.font.has_glyph(ord(ch)) or 0)
        return hit

    @property
    def serif(self) -> bool:
        return self.key.startswith("m")

    def font_data(self) -> bytes:
        data = _font_data.get(id(self.font))
        if data is None:
            try:
                data = bytes(self.font.buffer)
            except Exception:  # noqa: BLE001
                data = b""
            _font_data[id(self.font)] = data
        return data


_OPENING = set("（「『【〔〈《｛［〘〖〝‘“")
_CENTERED = set("：；・")


_faces: dict[tuple, Face | None] = {}
_builtin: dict[str, pymupdf.Font | None] = {}


def face_for(family: str | None, bold: bool = False, italic: bool = False) -> Face:
    serif = is_serif(family)
    key = (serif, bool(bold), bool(italic))
    face = _faces.get(key)
    if face is None:
        if serif:
            font, fake_bold = _load("BIZUDPMincho-Regular.ttf"), bold
        elif bold and _find("BIZUDPGothic-Bold.ttf"):
            font, fake_bold = _load("BIZUDPGothic-Bold.ttf"), False
        else:
            font, fake_bold = _load("BIZUDPGothic-Regular.ttf"), bold
        if font is None:   # the bundled faces could not be read: a built-in one still draws
            font, fake_bold = _builtin_font("cjk"), bold
        face = Face(f"{'m' if serif else 'g'}{'b' if bold else ''}{'i' if italic else ''}", font, fake_bold, italic)
        _faces[key] = face
    return face


def _builtin_font(name: str) -> pymupdf.Font | None:
    if name not in _builtin:
        try:
            _builtin[name] = pymupdf.Font(name)
        except Exception:  # noqa: BLE001
            _builtin[name] = None
    return _builtin[name]


_fallbacks: dict[tuple, Face | None] = {}


def fallback_face(face: Face, ch: str) -> Face:
    """A face that has ``ch``, or ``face`` itself when nothing does."""
    if face.has(ch):
        return face
    cache_key = (face.key, ch)
    if cache_key in _fallbacks:
        return _fallbacks[cache_key] or face
    found = None
    other = face_for("" if face.key.startswith("m") else "Mincho", face.fake_bold or "b" in face.key, face.fake_italic)
    if other is not face and other.has(ch):
        found = other
    else:
        font = _builtin_font("cjk")
        if font is not None and font.has_glyph(ord(ch)):
            found = Face("d" + face.key[1:], font, face.fake_bold, face.fake_italic)
    _fallbacks[cache_key] = found
    return found or face


# ----------------------------------------------------------------------------- styles

Color = tuple  # (r, g, b) floats 0..1


@dataclass(frozen=True)
class Style:
    family: str = "Yu Gothic"
    size: float = 10.5
    bold: bool = False
    italic: bool = False
    color: Color = (0.0, 0.0, 0.0)
    underline: str | None = None      # None, "single", "double", "dotted", "dashed", "wavy"
    strike: bool = False
    highlight: Color | None = None
    vert: int = 0                     # 1 superscript, -1 subscript
    spacing: float = 0.0              # extra pt between characters
    caps: bool = False
    link: str | None = None           # "uri:..." or "anchor:..."

    def effective_size(self) -> float:
        return self.size * (0.65 if self.vert else 1.0)

    def face(self) -> Face:
        return face_for(self.family, self.bold, self.italic)


def char_width(ch: str, style: Style) -> float:
    face = style.face()
    if not face.has(ch):
        face = fallback_face(face, ch)
    return face.advance(ch) * style.effective_size() + style.spacing


def text_width(text: str, style: Style) -> float:
    if style.caps:
        text = text.upper()
    return sum(char_width(ch, style) for ch in text)


def hex_color(value: str | None, default: Color | None = None) -> Color | None:
    if not value or value.lower() in ("auto", "none"):
        return default
    value = value.lstrip("#")
    if len(value) == 8:   # AARRGGBB
        value = value[2:]
    if len(value) != 6:
        return default
    try:
        return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return default


# ----------------------------------------------------------------------------- atoms and lines

# Characters that must not start a line / end a line (JIS X 4051 kinsoku, simplified).
NO_START = set("、。，．・：；？！）〕］｝〉》」』】〙〗〟’”ヽヾーァィゥェォッャュョヮヵヶぁぃぅぇぉっゃゅょゎゕゖ々〆"
               "!?,.:;)]}%‰’”…‥ゝゞ‐−–—～〜゛゜")
NO_END = set("（〔［｛〈《「『【〘〖〝‘“([{")


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF
            or 0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFFEF or 0x20000 <= o <= 0x2FFFF
            or 0x2190 <= o <= 0x23FF or 0x2460 <= o <= 0x27BF or 0x3000 <= o <= 0x303F)


@dataclass
class Atom:
    text: str
    style: Style
    width: float
    kind: str = "text"        # text, space, tab, br, image, field
    brk: bool = False         # a line may break after this atom (before the next)
    data: object = None       # image payload, field name, ...
    height: float = 0.0       # images
    x: float = 0.0            # filled in by place_line
    leader: str | None = None # tab leader character


def atomize(items: list[tuple]) -> list[Atom]:
    """Turn ``(kind, payload, style)`` items into atoms.

    kinds: ``text`` (str), ``tab``, ``br``, ``image`` ((w, h, data)), ``field`` (name, sample).
    """
    atoms: list[Atom] = []
    for item in items:
        kind, payload, style = item[0], item[1], item[2]
        if kind == "text":
            text = payload.upper() if style.caps else payload
            word: list[str] = []

            def flush(word=word, style=style):
                if word:
                    chunk = "".join(word)
                    atoms.append(Atom(chunk, style, text_width(chunk, style)))
                    word.clear()

            for ch in text:
                if ch in "\r\n\v":
                    flush()
                    atoms.append(Atom("", style, 0.0, "br"))
                elif ch == " " or ch == " ":
                    flush()
                    atoms.append(Atom(ch, style, char_width(" ", style), "space"))
                elif ch == "\t":
                    flush()
                    atoms.append(Atom("", style, 0.0, "tab"))
                elif is_cjk(ch):
                    flush()
                    atoms.append(Atom(ch, style, char_width(ch, style)))
                else:
                    word.append(ch)
                    if ch in "-/" and len(word) > 1:
                        flush()
            flush()
        elif kind == "tab":
            atoms.append(Atom("", style, 0.0, "tab"))
        elif kind == "br":
            atoms.append(Atom("", style, 0.0, "br", data=payload))
        elif kind == "image":
            w, h, data = payload
            atoms.append(Atom("", style, w, "image", data=data, height=h))
        elif kind == "field":
            name, sample = payload
            atoms.append(Atom(sample, style, text_width(sample, style), "field", data=name))
    _mark_breaks(atoms)
    return atoms


def _first(atom: Atom) -> str:
    return atom.text[:1]


def _last(atom: Atom) -> str:
    return atom.text[-1:]


def _mark_breaks(atoms: list[Atom]) -> None:
    for i, atom in enumerate(atoms):
        nxt = atoms[i + 1] if i + 1 < len(atoms) else None
        if nxt is None:
            atom.brk = True
            continue
        if atom.kind in ("br",):
            atom.brk = True
        elif atom.kind == "space" or atom.kind == "tab":
            atom.brk = True
        elif nxt.kind == "space":
            atom.brk = False
        elif atom.kind in ("image",) or nxt.kind in ("image", "tab"):
            atom.brk = True
        else:
            a, b = _last(atom), _first(nxt)
            if not a or not b:
                atom.brk = True
            elif b in NO_START or a in NO_END:
                atom.brk = False
            elif is_cjk(a) or is_cjk(b):
                atom.brk = True
            elif a in "-/":
                atom.brk = True
            else:
                atom.brk = False


@dataclass
class TabStop:
    pos: float
    align: str = "left"       # left, right, center, decimal, bar
    leader: str | None = None


@dataclass
class Line:
    atoms: list[Atom]
    width: float = 0.0
    ascent: float = 0.0       # above baseline
    descent: float = 0.0
    last: bool = False        # last line of the paragraph (no justification)
    forced: bool = False      # ends with a hard break
    indent: float = 0.0       # x offset of the line start from the left margin
    avail: float = 0.0
    gap: float = 0.0          # empty space above the line (text pushed down by a picture)


def _tab_target(x: float, tabs: list[TabStop], default: float, origin: float = 0.0):
    for stop in tabs:
        if stop.pos > x + 0.01:
            return stop
    step = default if default > 1 else 36.0
    nxt = (int((x - origin) / step) + 1) * step + origin
    return TabStop(nxt, "left")


def _wf(width_for, i, lines):
    res = width_for(i, lines)
    return (res[0], res[1], res[2] if len(res) > 2 else 0.0)


def break_lines(atoms: list[Atom], width_for, tabs: list[TabStop] | None = None,
                default_tab: float = 36.0, first_offset: float = 0.0) -> list[Line]:
    """Greedy line breaking.

    ``width_for(i, lines)`` gives (offset, available width) of line ``i`` (``lines`` are the
    lines finished so far): the hanging and first-line indents are expressed by it. Positions are relative to the
    text area's left edge.
    """
    tabs = sorted(tabs or [], key=lambda s: s.pos)
    lines: list[Line] = []
    current: list[Atom] = []
    x = 0.0
    last_break = -1          # index into current: break allowed after it
    offset, avail, gap = _wf(width_for, 0, lines)

    def finish(forced=False, last=False):
        nonlocal current, x, last_break, offset, avail, gap
        line = Line(current, indent=offset, avail=avail, forced=forced, last=last or forced, gap=gap)
        lines.append(line)
        current = []
        x = 0.0
        last_break = -1
        offset, avail, gap = _wf(width_for, len(lines), lines)

    i = 0
    n = len(atoms)
    while i < n:
        atom = atoms[i]
        if atom.kind == "br":
            current.append(atom)
            finish(forced=True)
            i += 1
            continue
        if atom.kind == "tab":
            stop = _tab_target(x + offset, tabs, default_tab, 0.0)
            target = stop.pos - offset
            if stop.align in ("right", "center", "decimal"):
                seg = 0.0
                j = i + 1
                while j < n and atoms[j].kind not in ("tab", "br"):
                    seg += atoms[j].width
                    j += 1
                target = stop.pos - offset - (seg if stop.align == "right" else seg / 2)
            target = max(target, x)
            atom.leader = stop.leader
            atom.width = target - x
            if target > avail + 0.5 and current:
                # a tab past the right edge ends the line at the edge
                atom.width = max(0.0, avail - x)
            current.append(atom)
            x += atom.width
            last_break = len(current) - 1
            i += 1
            continue
        fits = x + atom.width <= avail + 0.01
        if atom.kind == "space":
            fits = True        # a space may hang past the edge
        if not fits and atom.text and _first(atom) in NO_START and len(atom.text) == 1 and current:
            fits = x + atom.width <= avail + atom.style.size * 1.01   # punctuation hangs
        if fits:
            current.append(atom)
            x += atom.width
            if atom.brk:
                last_break = len(current) - 1
            i += 1
            continue
        # does not fit
        if last_break >= 0 and last_break < len(current) - 1 + 1 and current:
            keep = current[:last_break + 1]
            carry = current[last_break + 1:]
            current = keep
            finish()
            current = carry
            x = sum(a.width for a in carry)
            last_break = -1
            for k, a in enumerate(current):
                if a.brk:
                    last_break = k
            continue          # try the same atom again on the new line
        if current:
            finish()
            continue
        # a single atom wider than the line: split it by characters
        if atom.kind == "text" and len(atom.text) > 1:
            acc = ""
            w = 0.0
            for ch in atom.text:
                cw = char_width(ch, atom.style)
                if acc and w + cw > avail:
                    break
                acc += ch
                w += cw
            rest = atom.text[len(acc):]
            first = Atom(acc, atom.style, w, "text", brk=True)
            second = Atom(rest, atom.style, text_width(rest, atom.style), "text", brk=atom.brk)
            atoms[i:i + 1] = [first, second]
            n += 1
            continue
        current.append(atom)
        x += atom.width
        i += 1
    if current or not lines:
        finish(last=True)
    lines[-1].last = True
    for line in lines:
        trim = 0.0
        for a in reversed(line.atoms):
            if a.kind == "space":
                trim += a.width
            else:
                break
        line.width = sum(a.width for a in line.atoms) - trim
    return lines


def line_metrics(line: Line, spacing_rule: tuple[str, float] | None, grid: float = 0.0, app: str = "word") -> tuple[float, float]:
    """Return (line height, baseline offset from the line top)."""
    natural = 0.0
    size_max = 0.0
    img = 0.0
    for atom in line.atoms:
        if atom.kind == "image":
            img = max(img, atom.height)
            continue
        size = atom.style.effective_size() if atom.kind not in ("br", "mark") else atom.style.size
        size_max = max(size_max, size)
        natural = max(natural, size * line_factor(atom.style.family, app))
    if not line.atoms or (natural == 0 and img == 0):
        natural = 10.5 * 1.22
        size_max = 10.5
    if grid > 0 and natural > 0:
        # a character grid ("lines" document grid): lines sit on multiples of the pitch
        steps = max(1, round(natural / grid + 0.0001))
        natural = max(natural, grid) if natural <= grid else steps * grid
    height = max(natural, img + size_max * 0.2 if img else 0)
    rule, value = spacing_rule or ("auto", 1.0)
    if rule == "auto":
        height *= value
    elif rule == "exact":
        height = value
    elif rule == "atLeast":
        height = max(height, value)
    # baseline: centre the em box in the line; an image sits on the baseline
    if img and img >= size_max:
        base = height - size_max * 0.2 if rule != "exact" else height * 0.8
        return height, max(img, base) if rule != "exact" else base
    base = (height - size_max) / 2 + size_max * 0.88
    if rule == "exact":
        base = height * 0.8
    return height, base


def place_line(line: Line, align: str, avail: float, justify_ok: bool = True, clamp: bool = True) -> None:
    """Set ``atom.x`` for each atom of a laid-out line (relative to the line's left edge)."""
    atoms = line.atoms
    width = line.width
    extra_gaps = 0
    if align in ("both", "distribute") and justify_ok and (not line.last or align == "distribute") and not line.forced:
        # distribute the slack over the gaps (spaces, and between CJK characters)
        slack = avail - width
        gaps = []
        end = len(atoms)
        while end > 0 and atoms[end - 1].kind == "space":
            end -= 1
        for k in range(end - 1):
            a, b = atoms[k], atoms[k + 1]
            if a.kind == "tab" or b.kind == "tab":
                continue
            if a.kind == "space" or (a.brk and b.kind != "space"):
                gaps.append(k)
        if slack > 0 and gaps and slack < avail * 0.5:
            per = slack / len(gaps)
            x = 0.0
            gapset = set(gaps)
            for k, atom in enumerate(atoms):
                atom.x = x
                x += atom.width + (per if k in gapset else 0.0)
            line.width = x
            return
    if align == "right":
        x0 = avail - width
    elif align == "center":
        x0 = (avail - width) / 2
    else:
        x0 = 0.0
    x = (max(x0, 0.0) if clamp else x0) if align in ("right", "center") else x0
    for atom in atoms:
        atom.x = x
        x += atom.width


# ----------------------------------------------------------------------------- display list

@dataclass
class Sheet:
    """One output page: its size and what is drawn on it, in drawing order."""

    width: float
    height: float
    ops: list[tuple] = field(default_factory=list)
    below: list[tuple] = field(default_factory=list)    # drawn before ops (page background)

    def rect(self, x0, y0, x1, y1, fill=None, stroke=None, width=0.5, dash=None, radius=None):
        self.ops.append(("rect", x0, y0, x1, y1, fill, stroke, width, dash, radius))

    def line(self, x0, y0, x1, y1, color=(0, 0, 0), width=0.5, dash=None):
        self.ops.append(("line", x0, y0, x1, y1, color, width, dash))

    def text(self, x, baseline, text, style: Style):
        self.ops.append(("text", x, baseline, text, style))

    def image(self, x0, y0, x1, y1, data, clip=None):
        self.ops.append(("image", x0, y0, x1, y1, data, clip))

    def link(self, x0, y0, x1, y1, target):
        self.ops.append(("link", x0, y0, x1, y1, target))

    def poly(self, points, fill=None, stroke=None, width=0.5, closed=True, dash=None):
        self.ops.append(("poly", points, fill, stroke, width, closed, dash))


def emit_line(sheet: Sheet, line: Line, x_left: float, baseline: float, line_top: float, line_height: float) -> None:
    """Draw the atoms of a placed line: backgrounds, text, decorations, links."""
    run_text: list[str] = []
    run_style: Style | None = None
    run_x = 0.0
    run_end = 0.0

    def flush():
        nonlocal run_text, run_style
        if run_style is not None and run_text:
            _emit_run(sheet, run_x, baseline, "".join(run_text), run_style, run_end - run_x, line_top, line_height)
        run_text = []
        run_style = None

    for atom in line.atoms:
        if atom.kind in ("br", "mark"):
            continue
        x = x_left + atom.x
        if atom.kind == "tab":
            flush()
            if atom.leader and atom.width > 2:
                dot = atom.leader if atom.leader != "dot" else "."
                dot = {"hyphen": "-", "underscore": "_", "middleDot": "·", "heavy": "_"}.get(dot, dot)
                style = atom.style
                step = max(char_width(dot, style), 0.1)
                count = int((atom.width - 2) / step)
                text = dot * count
                if text:
                    sheet.text(x + atom.width - count * step - 1, baseline, text, style)
            continue
        if atom.kind == "image":
            flush()
            if callable(atom.data):
                atom.data(sheet, x, baseline - atom.height)
            elif atom.data is not None:
                sheet.image(x, baseline - atom.height, x + atom.width, baseline, atom.data)
            continue
        if atom.kind == "space" and run_style is None:
            # spaces only matter inside a run (decorations); skip leading ones
            if atom.style.highlight is None and atom.style.underline is None and not atom.style.strike:
                continue
        text = atom.text if atom.kind != "field" else "{%s}" % atom.data
        if run_style is not None and (atom.style != run_style or abs(x - run_end) > 0.6):
            flush()
        if run_style is None:
            run_style = atom.style
            run_x = x
        run_text.append(text)
        run_end = x + atom.width
    flush()


def _emit_run(sheet: Sheet, x: float, baseline: float, text: str, style: Style, width: float,
              line_top: float, line_height: float) -> None:
    size = style.effective_size()
    shift = 0.0
    if style.vert == 1:
        shift = -style.size * 0.35
    elif style.vert == -1:
        shift = style.size * 0.12
    y = baseline + shift
    if style.highlight is not None and width > 0:
        sheet.rect(x, line_top, x + width, line_top + line_height, fill=style.highlight)
    shown = text.rstrip(" ") if style.underline is None and not style.strike else text
    if shown.strip() or style.underline or style.strike:
        if style.caps:
            shown = shown.upper()
        sheet.text(x, y, shown, style)
    if style.underline and width > 0:
        yy = y + size * 0.12
        thickness = max(0.4, size * 0.05)
        dash = {"dotted": "[1 1.5] 0", "dash": "[3 2] 0", "dashed": "[3 2] 0"}.get(style.underline)
        sheet.line(x, yy, x + width, yy, style.color, thickness, dash)
        if style.underline == "double":
            sheet.line(x, yy + thickness * 2.2, x + width, yy + thickness * 2.2, style.color, thickness)
    if style.strike and width > 0:
        yy = y - size * 0.30
        sheet.line(x, yy, x + width, yy, style.color, max(0.4, size * 0.05))
    if style.link:
        sheet.link(x, line_top, x + width, line_top + line_height, style.link)


# ----------------------------------------------------------------------------- drawing into a PDF

def render(sheets: list[Sheet], doc: pymupdf.Document | None = None, subset: bool = True,
           fields: dict | None = None, anchors: dict | None = None) -> pymupdf.Document:
    """Draw display lists into ``doc`` (a new document by default)."""
    from .rawpdf import render_sheets
    return render_sheets(sheets, doc, subset, fields or {}, anchors or {})
