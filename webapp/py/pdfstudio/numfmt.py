"""Excel number formats: turning a cell's value into the text Excel would show."""

from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Decimal

BUILTIN = {
    0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 9: "0%", 10: "0.00%", 11: "0.00E+00", 12: "# ?/?", 13: "# ??/??",
    14: "yyyy/m/d", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy", 18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss",
    22: "yyyy/m/d h:mm", 27: 'ggge"年"m"月"', 28: 'ggge"年"m"月"d"日"', 29: 'ggge"年"m"月"d"日"', 30: "m/d/yy", 31: 'yyyy"年"m"月"d"日"',
    32: 'h"時"mm"分"', 33: 'h"時"mm"分"ss"秒"', 34: 'yyyy"年"m"月"', 35: 'm"月"d"日"', 36: 'ggge"年"m"月"d"日"',
    37: "#,##0 ;(#,##0)", 38: "#,##0 ;[Red](#,##0)", 39: "#,##0.00;(#,##0.00)", 40: "#,##0.00;[Red](#,##0.00)",
    41: '#,##0;-#,##0', 42: '"¥"#,##0;-"¥"#,##0', 43: "#,##0.00;-#,##0.00", 44: '"¥"#,##0.00;-"¥"#,##0.00',
    45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0", 48: "##0.0E+0", 49: "@",
    50: 'ggge"年"m"月"d"日"', 51: 'ggge"年"m"月"d"日"', 52: 'yyyy"年"m"月"', 53: 'm"月"d"日"', 54: 'ggge"年"m"月"d"日"',
    55: 'yyyy"年"m"月"', 56: 'm"月"d"日"', 57: 'ggge"年"m"月"d"日"', 58: 'ggge"年"m"月"d"日"',
}

_WEEK_JA = "月火水木金土日"
_WEEK_JA_LONG = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
_WEEK_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTH_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

_ERAS = [  # (start date, long, short, letter)
    (_dt.date(2019, 5, 1), "令和", "令", "R"), (_dt.date(1989, 1, 8), "平成", "平", "H"), (_dt.date(1926, 12, 25), "昭和", "昭", "S"),
    (_dt.date(1912, 7, 30), "大正", "大", "T"), (_dt.date(1868, 1, 25), "明治", "明", "M"),
]


def serial_to_datetime(value: float, date1904: bool = False) -> _dt.datetime:
    if date1904:
        return _dt.datetime(1904, 1, 1) + _dt.timedelta(days=value)
    if value < 60:
        return _dt.datetime(1899, 12, 31) + _dt.timedelta(days=value)
    return _dt.datetime(1899, 12, 30) + _dt.timedelta(days=value)


def general(value: float) -> str:
    if value == int(value) and abs(value) < 1e11:
        return str(int(value))
    text = f"{value:.10g}"
    if "e" in text:
        mant, exp = text.split("e")
        sign = exp[0] if exp[0] in "+-" else "+"
        digits = exp.lstrip("+-").lstrip("0") or "0"
        return f"{mant}E{sign}{int(digits):02d}"
    return text


def _split_sections(code: str) -> list[str]:
    out, cur, in_q, in_b, esc = [], "", False, False, False
    for ch in code:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            cur += ch
            esc = True
        elif ch == '"':
            in_q = not in_q
            cur += ch
        elif ch == "[" and not in_q:
            in_b = True
            cur += ch
        elif ch == "]" and not in_q:
            in_b = False
            cur += ch
        elif ch == ";" and not in_q and not in_b:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


_COND = re.compile(r"\[(<=|>=|<>|<|>|=)\s*(-?[\d.]+)\]")
_COLOR = re.compile(r"\[(Black|Blue|Cyan|Green|Magenta|Red|White|Yellow|Color\s*\d+)\]", re.I)
COLORS = {"red": (1, 0, 0), "blue": (0, 0, 1), "green": (0, 0.5, 0), "black": (0, 0, 0), "cyan": (0, 1, 1), "magenta": (1, 0, 1),
          "white": (1, 1, 1), "yellow": (1, 1, 0)}


def _is_date_format(section: str) -> bool:
    s = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", section)
    s = re.sub(r"[Ee][+-]", "", s)
    return bool(re.search(r"[ymdhsgeaAbB]", s)) and not re.search(r"^[#0?,.%\s]*$", s)


def _round(value: float, decimals: int) -> Decimal:
    q = Decimal(1).scaleb(-decimals)
    return Decimal(repr(value)).quantize(q, rounding=ROUND_HALF_UP)


def _tokenise(section: str):
    """Split a format section into ('lit', text) / ('ph', chars) tokens, keeping placeholders together."""
    tokens = []
    i = 0
    n = len(section)
    ph = ""
    def flush():
        nonlocal ph
        if ph:
            tokens.append(("ph", ph))
            ph = ""
    while i < n:
        ch = section[i]
        if ch == '"':
            flush()
            j = section.find('"', i + 1)
            j = n if j < 0 else j
            tokens.append(("lit", section[i + 1:j]))
            i = j + 1
        elif ch == "\\":
            flush()
            if i + 1 < n:
                tokens.append(("lit", section[i + 1]))
            i += 2
        elif ch == "_":
            flush()
            tokens.append(("lit", " "))
            i += 2
        elif ch == "*":
            flush()
            i += 2
        elif ch == "[":
            j = section.find("]", i)
            j = n if j < 0 else j
            body = section[i + 1:j]
            flush()
            m = re.match(r"\$([^-\]]*)", body)
            if m:
                tokens.append(("lit", m.group(1)))
            i = j + 1
        elif ch in "#0?.,":
            ph += ch
            i += 1
        elif ch == "%":
            flush()
            tokens.append(("pct", "%"))
            i += 1
        elif ch in "Ee" and i + 1 < n and section[i + 1] in "+-":
            flush()
            tokens.append(("exp", section[i:i + 2]))
            i += 2
        elif ch == "@":
            flush()
            tokens.append(("text", "@"))
            i += 1
        else:
            flush()
            tokens.append(("lit", ch))
            i += 1
    flush()
    return tokens


def format_value(value, kind: str, code: str, date1904: bool = False):
    """Return (text, colour or None, is_numeric). ``kind`` is 'n' for numbers, 's' for text."""
    if kind == "s" or not isinstance(value, (int, float)):
        text = str(value)
        sections = _split_sections(code)
        if len(sections) >= 4 or (len(sections) >= 1 and "@" in code):
            sec = sections[3] if len(sections) >= 4 else sections[0]
            if "@" in sec:
                out = ""
                for tok in _tokenise(sec):
                    out += text if tok[0] == "text" else tok[1] if tok[0] in ("lit",) else ""
                return out, None, False
        return text, None, False
    if code in ("General", "", "標準"):
        return general(value), None, True
    sections = _split_sections(code)
    color = None
    chosen = sections[0]
    negative = False
    if len(sections) > 1 or _COND.search(code):
        conds = []
        for sec in sections[:3]:
            m = _COND.search(sec)
            conds.append((m.group(1), float(m.group(2))) if m else None)
        picked = None
        if any(conds):
            default = [None] * len(sections)
            for idx, c in enumerate(conds):
                if c and _cmp(value, c[0], c[1]):
                    picked = idx
                    break
            if picked is None:
                picked = len(sections) - 1 if len(sections) > 1 else 0
            chosen = sections[picked]
            if picked == 1 and not conds[1]:
                negative = True
        else:
            if value > 0 or (value == 0 and len(sections) < 3):
                chosen = sections[0]
            elif value < 0:
                chosen = sections[1] if len(sections) > 1 else sections[0]
                negative = len(sections) > 1
            else:
                chosen = sections[2] if len(sections) > 2 else sections[0]
    m = _COLOR.search(chosen)
    if m:
        color = COLORS.get(m.group(1).lower())
    section = _COND.sub("", _COLOR.sub("", chosen))
    if _is_date_format(section):
        return _format_date(value, section, date1904), color, False
    v = abs(value) if (negative or (len(sections) > 1 and value < 0)) else value
    return _format_number(v, section, value < 0 and len(sections) == 1), color, True


def _cmp(value, op, num):
    return {"<": value < num, ">": value > num, "<=": value <= num, ">=": value >= num, "=": value == num, "<>": value != num}[op]


def _format_number(value: float, section: str, minus: bool) -> str:
    tokens = _tokenise(section)
    percent = sum(1 for t in tokens if t[0] == "pct")
    exp_index = next((i for i, t in enumerate(tokens) if t[0] == "exp"), None)
    limit = exp_index if exp_index is not None else len(tokens)
    ph_tokens = [i for i, t in enumerate(tokens[:limit]) if t[0] == "ph"]
    if not ph_tokens:
        out = "".join(t[1] if t[0] in ("lit", "pct") else "" for t in tokens)
        return ("-" if minus else "") + out
    first, last = ph_tokens[0], ph_tokens[-1]
    # the numeric region, keeping literal text that sits between placeholders (e.g. 000-0000)
    items: list[tuple[str, str]] = []
    for t in tokens[first:last + 1]:
        if t[0] == "ph":
            items.extend(("p", c) for c in t[1])
        elif t[0] in ("lit", "pct"):
            items.append(("l", t[1]))
    dot = next((i for i, it in enumerate(items) if it == ("p", ".")), None)
    int_items = items if dot is None else items[:dot]
    frac_items = [] if dot is None else items[dot + 1:]
    scale_commas = 0
    if dot is None:
        while int_items and int_items[-1] == ("p", ","):
            int_items.pop()
            scale_commas += 1
    if percent:
        value = value * 100
    value = value / (1000 ** scale_commas)
    if exp_index is not None:
        mant_pat = "".join(c for k, c in int_items if k == "p") + ("." + "".join(c for k, c in frac_items if k == "p") if dot is not None else "")
        exp_pat = "".join(t[1] for t in tokens[exp_index + 1:] if t[0] == "ph")
        return ("-" if minus else "") + _sci(value, mant_pat, exp_pat)
    decimals = sum(1 for k, c in frac_items if k == "p" and c in "0#?")
    thousands = any(it == ("p", ",") for it in int_items)
    int_clean = [it for it in int_items if it != ("p", ",")]
    rounded = _round(value, decimals)
    text = f"{rounded:f}"
    int_digits, _, frac_digits = text.partition(".")
    int_digits = int_digits.lstrip("-")
    min_int = sum(1 for k, c in int_clean if k == "p" and c == "0")
    if int_digits == "0" and min_int == 0:
        int_digits = ""
    int_digits = int_digits.rjust(min_int, "0")
    # fraction digits: '0' and '?' keep their digit, '#' drops a trailing zero
    frac_out = ""
    pholders = [c for k, c in frac_items if k == "p"]
    for idx, c in enumerate(pholders):
        frac_out += frac_digits[idx] if idx < len(frac_digits) else "0"
    k = len(pholders)
    while k > 0 and pholders[k - 1] == "#" and frac_out[k - 1:k] == "0":
        k -= 1
        frac_out = frac_out[:k]
    literal_inside = any(kind == "l" for kind, _c in int_clean)
    if literal_inside:
        digits = list(int_digits)
        out_chars: list[str] = []
        for kind, c in reversed(int_clean):
            if kind == "p":
                out_chars.append(digits.pop() if digits else ("0" if c == "0" else ""))
            else:
                out_chars.append(c)
        out_chars.extend(reversed(digits))
        int_text = "".join(reversed(out_chars))
    else:
        int_text = int_digits
        if thousands and int_text.isdigit():
            int_text = f"{int(int_text):,}"
        q_int = sum(1 for kind, c in int_clean if kind == "p" and c == "?")
        if q_int and len(int_text) < len(int_clean):
            int_text = int_text.rjust(len(int_clean), " ")
    num = int_text + ("." + frac_out if dot is not None and (decimals or frac_items) and frac_out else "")
    pre = "".join(t[1] for t in tokens[:first] if t[0] in ("lit", "pct"))
    post = "".join(t[1] if t[0] in ("lit", "pct") else "" for t in tokens[last + 1:])
    out = pre + num + post
    if minus and rounded != 0:
        out = "-" + out
    return out


def _sci(value: float, mant_pat: str, exp_pat: str) -> str:
    import math
    decimals = len(mant_pat.partition(".")[2])
    exp_digits = max(len(exp_pat), 1)
    if value == 0:
        mant, exp = 0.0, 0
    else:
        exp = int(math.floor(math.log10(abs(value))))
        mant = value / (10 ** exp)
        if round(mant, decimals) >= 10:
            mant /= 10
            exp += 1
    sign = "+" if exp >= 0 else "-"
    return f"{mant:.{decimals}f}E{sign}{abs(exp):0{exp_digits}d}"


def _format_date(value: float, section: str, date1904: bool) -> str:
    try:
        dt = serial_to_datetime(value, date1904)
    except (OverflowError, ValueError):
        return "#VALUE!"
    elapsed_hours = None
    toks = re.findall(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.|ggg+|gg|g|ee|e|yyyy|yy|mmmmm|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|AM/PM|am/pm|A/P|a/p|aaaa|aaa|\.0+|.', section, re.I)
    # find whether "m" is a minute: right after h or right before s
    meaningful = [t for t in toks if t.lower() not in ("", ":", " ")]
    out = []
    ampm = any(t.lower() in ("am/pm", "a/p") for t in toks)
    hour = dt.hour
    prev_unit = None
    for idx, t in enumerate(toks):
        tl = t.lower()
        if t.startswith('"'):
            out.append(t[1:-1])
        elif t.startswith("\\"):
            out.append(t[1:])
        elif t.startswith("["):
            inner = t[1:-1].lower()
            if inner in ("h", "hh"):
                total_h = int((value) * 24) if True else 0
                out.append(str(total_h))
                prev_unit = "h"
            elif inner in ("m", "mm"):
                out.append(str(int(value * 24 * 60)))
                prev_unit = "m"
            elif inner in ("s", "ss"):
                out.append(str(int(round(value * 24 * 3600))))
            elif inner.startswith("$"):
                out.append(inner[1:].split("-")[0])
        elif t.startswith("_"):
            out.append(" ")
        elif t.startswith("*"):
            pass
        elif tl == "yyyy":
            out.append(f"{dt.year:04d}")
        elif tl == "yy":
            out.append(f"{dt.year % 100:02d}")
        elif tl in ("ggg", "gg", "g", "e", "ee"):
            era = next((e for e in _ERAS if dt.date() >= e[0]), None)
            if tl == "ggg":
                out.append(era[1] if era else "")
            elif tl == "gg":
                out.append(era[2] if era else "")
            elif tl == "g":
                out.append(era[3] if era else "")
            else:
                yr = dt.year - era[0].year + 1 if era else dt.year
                out.append(f"{yr:02d}" if tl == "ee" else str(yr))
        elif tl in ("m", "mm"):
            before = next((x.lower() for x in reversed(toks[:idx]) if x.lower() in ("h", "hh", "s", "ss", "d", "dd", "yy", "yyyy") or x.lower().startswith("[h")), None)
            after = next((x.lower() for x in toks[idx + 1:] if x.lower() in ("s", "ss", "h", "hh", "d", "dd", "yy", "yyyy")), None)
            is_minute = (before in ("h", "hh") or (before or "").startswith("[h")) or (after in ("s", "ss") and before not in ("d", "dd", "yy", "yyyy"))
            if is_minute:
                out.append(f"{dt.minute:02d}" if tl == "mm" else str(dt.minute))
            else:
                out.append(f"{dt.month:02d}" if tl == "mm" else str(dt.month))
        elif tl == "mmm":
            out.append(_MONTH_EN[dt.month - 1][:3])
        elif tl == "mmmm":
            out.append(_MONTH_EN[dt.month - 1])
        elif tl == "mmmmm":
            out.append(_MONTH_EN[dt.month - 1][0])
        elif tl in ("d", "dd"):
            out.append(f"{dt.day:02d}" if tl == "dd" else str(dt.day))
        elif tl == "ddd":
            out.append(_WEEK_EN[dt.weekday()][:3])
        elif tl == "dddd":
            out.append(_WEEK_EN[dt.weekday()])
        elif tl == "aaa":
            out.append(_WEEK_JA[dt.weekday()])
        elif tl == "aaaa":
            out.append(_WEEK_JA_LONG[dt.weekday()])
        elif tl in ("h", "hh"):
            h = hour % 12 or 12 if ampm else hour
            out.append(f"{h:02d}" if tl == "hh" else str(h))
        elif tl in ("s", "ss"):
            out.append(f"{dt.second:02d}" if tl == "ss" else str(dt.second))
        elif tl in ("am/pm", "a/p"):
            if tl == "am/pm":
                out.append("AM" if dt.hour < 12 else "PM")
            else:
                out.append("A" if dt.hour < 12 else "P")
        elif tl.startswith(".0"):
            out.append("." + f"{dt.microsecond // 1000:03d}"[: len(tl) - 1])
        else:
            out.append(t)
    return "".join(out)
