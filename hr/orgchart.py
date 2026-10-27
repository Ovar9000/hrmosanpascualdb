"""Org charts: the municipal office tree and per-office staff charts (DECISIONS O1-O6).

One layout feeds every output, so the page, PNG, PDF and PowerPoint always match:
the build functions turn records into ChartNode trees, layout() places the boxes and
connector lines, and the render functions draw the placed chart.

Who may see what is decided by the callers (hr/permissions.py); the builders only take
the viewer's flags (show types, which people to link) and never add pay or personal data.
"""

import io
import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from reportlab.pdfbase.pdfmetrics import stringWidth

from .models import Engagement, Office, OrgUnit, StructurePosition

# --- Look ---------------------------------------------------------------------------

BOX_W = 200
PAD_X, PAD_Y, ACCENT = 10, 8, 4
LINE_SPACING = 1.3
H_GAP, V_GAP = 20, 36  # between sibling subtrees / from a parent to a row of children
SIDE_GAP, ROW_GAP = 22, 12  # trunk to a hanging box / between hanging boxes
COTERM_GAP, DASH = 40, (5, 4)  # head box to a coterminous box beside it, and that line's dashes
GRID_ROW_MAX, GRID_SIDE, TRUNK_GAP = 6, 3, 40  # office tree: more than 6 divisions go in rows of 3 + 3
HEAD_W, HEAD_TEXT = 280, 1.2  # the head box at the top of the office tree: wider, larger text
STACK_MIN = 3  # this many childless children hang in a column instead of a wide row
TWO_SIDED_MIN = 5  # and this many hang on both sides of the trunk
MARGIN = 16
REGULAR, BOLD = "Helvetica", "Helvetica-Bold"
# Structure charts (DECISIONS O7) look like HRMO's own: square boxes filled with the legend colour,
# each position under the one it reports to, and the head's own staff in a row just below its box.
POS_W, SIDE_W, FLAT_HEAD_W = 136, 156, 200
FLAT_PAD_X, FLAT_PAD_Y, POS_MIN_H = 6, 6, 46
FLAT_V_GAP, SIDE_DROP, SIDE_ROW_GAP = 22, 14, 30  # a box to the one under it / head to its staff row
FLAT_HEAD_TEXT = 13
PLACEHOLDER = "placeholder"  # an empty section: a dashed box where its positions will go
DIVISIONS_PER_SLIDE = 3  # PDF and PowerPoint split a wider structure chart, as HRMO's slides do
FIT_W, FIT_H = 1600, 800  # the page and PNG wrap a structure chart's divisions into rows to fit this shape

INK, MUTED, LINE, FLAT_LINE = "#212529", "#6c757d", "#adb5bd", "#4472c4"
# accent (border and top band) per style; elected/plantilla/... for people, branches for offices
STYLES = {
    Engagement.Type.ELECTED: "#198754",
    Engagement.Type.PLANTILLA: "#0d6efd",
    Engagement.Type.CASUAL: "#0aa2c0",
    Engagement.Type.CONTRACTUAL: "#6f42c1",
    Office.Branch.EXECUTIVE: "#0d6efd",
    Office.Branch.LEGISLATIVE: "#087990",
    Office.Branch.OTHER: "#6c757d",
    "root": "#212529",
}
# Structure charts follow the legend of the office's own chart (DECISIONS O7): (border, fill, text).
# Red is a vacancy (proposed for creation), green a position that exists and is filled.
STRUCTURE_STYLES = {
    StructurePosition.Status.ELECTED: ("#e46c0a", "#ffc000", INK),
    StructurePosition.Status.PROPOSED: ("#a50000", "#e00000", "#ffff00"),
    StructurePosition.Status.FILLED: ("#548235", "#a9d18e", INK),
    OrgUnit.Kind.DIVISION: ("#70ad47", "#ffffff", INK),
    OrgUnit.Kind.SECTION: ("#70ad47", "#ffffff", INK),
    OrgUnit.Kind.BODY: ("#2e75b6", "#9dc3e6", INK),
    PLACEHOLDER: (LINE, "#ffffff", MUTED),
}
STYLES.update({key: border for key, (border, _fill, _text) in STRUCTURE_STYLES.items()})
FILLS = {key: fill for key, (_border, fill, _text) in STRUCTURE_STYLES.items()}
TEXTS = {key: text for key, (_border, _fill, text) in STRUCTURE_STYLES.items()}
FLAT_TEXT = {OrgUnit.Kind.DIVISION: 10.5, OrgUnit.Kind.SECTION: 8, OrgUnit.Kind.BODY: 9, PLACEHOLDER: 8}  # else 8.5
STRUCTURE_LEGEND = [
    (StructurePosition.Status.ELECTED.label, StructurePosition.Status.ELECTED),
    (StructurePosition.Status.PROPOSED.label, StructurePosition.Status.PROPOSED),
    (StructurePosition.Status.FILLED.label, StructurePosition.Status.FILLED),
    (OrgUnit.Kind.BODY.label, OrgUnit.Kind.BODY),
]
HIGHLIGHT_FILL, HIGHLIGHT_BORDER = "#fff3cd", "#b58105"

TYPE_ORDER = {t: i for i, t in enumerate(Engagement.CHART_TYPES)}


@dataclass
class ChartNode:
    title: str
    subtitle: str = ""
    detail: str = ""
    style: str = "root"
    url: str = ""
    badge: str = ""  # "You" / "Your supervisor": drawn highlighted
    tag: str = ""  # small grey label above the title, e.g. "Coterminous"
    more: str = ""  # collapsed office tree box: what is folded under it
    grid: bool = False  # office tree: childless children in rows across the page, never hanging
    emphasis: bool = False  # the head at the top: filled with its colour, white text
    flat: bool = False  # structure chart: a square box filled with its legend colour, text centred
    span: bool = False  # structure chart: a division or section box as wide as everything under it
    side_row: bool = False  # the side boxes go in a row just below this box, not in columns beside it
    rows: bool = False  # structure chart: the children wrap into rows either side of the trunk to fit a screen
    dashed: bool = False  # the line to this box is dashed (coterminous)
    children: list = field(default_factory=list)
    joined: list = field(default_factory=list)  # under the row of children, joined to the bottom of each
    side: list = field(default_factory=list)  # coterminous: beside this box, not below it
    # Filled in by layout(): position, size and the wrapped text lines
    # as (text, font, size, colour, baseline offset from the box top).
    x: float = 0
    y: float = 0
    w: float = BOX_W
    h: float = 0
    lines: list = field(default_factory=list)

    @property
    def accent(self):
        return HIGHLIGHT_BORDER if self.badge else STYLES.get(self.style, MUTED)

    @property
    def fill(self):
        if self.emphasis and not self.flat:
            return self.accent
        return HIGHLIGHT_FILL if self.badge else FILLS.get(self.style, "#ffffff")

    @property
    def band(self):
        """The coloured strip along the top of the box."""
        return not self.emphasis and not self.flat

    @property
    def vgap(self):
        return FLAT_V_GAP if self.flat else V_GAP

    @property
    def radius(self):
        return 0 if self.flat else 5

    @property
    def outline_dashed(self):
        return self.style == PLACEHOLDER

    def ink(self, colour):
        """Text colour on this box: dark on a light filled head box, white and light grey on a dark one,
        and the legend's own text colour on a structure box (yellow on red)."""
        if self.flat:
            text = TEXTS.get(self.style, INK)
            return colour if text == INK else text
        if not self.emphasis:
            return colour
        if _light(self.accent):
            return INK
        return "#dee2e6" if colour == MUTED else "#ffffff"


def _light(colour):
    r, g, b = (int(colour.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b > 160


@dataclass
class Edge:
    parent: ChartNode
    child: ChartNode
    # "below" (child in a row underneath, or a joined box under a column's last box),
    # "right" / "left" (child hangs beside the trunk, or the first box of the head's side row),
    # "side-right" / "side-left" (child beside the parent's box, line from its side)
    kind: str

    @property
    def dashed(self):
        return self.child.dashed

    @property
    def colour(self):
        return FLAT_LINE if self.parent.flat else LINE

    @property
    def points(self):
        p, c = self.parent, self.child
        px, pb = p.x + p.w / 2, p.y + p.h
        if self.kind == "below":
            cx, mid = c.x + c.w / 2, c.y - p.vgap / 2  # just above the row, below any side band
            if p.span and len(p.children) == 1 and p.children[0] is c:
                return [(cx, pb), (cx, c.y)]  # the box spans what is under it: straight down
            return [(px, pb), (px, mid), (cx, mid), (cx, c.y)]
        cy = c.y + c.h / 2
        if self.kind.startswith("side-"):
            right = self.kind == "side-right"
            sx, sy = (p.x + p.w if right else p.x), p.y + p.h / 2
            ex = c.x if right else c.x + c.w
            if abs(sy - cy) < 0.01:
                return [(sx, sy), (ex, cy)]
            mid = (sx + ex) / 2
            return [(sx, sy), (mid, sy), (mid, cy), (ex, cy)]
        end_x = c.x if self.kind == "right" else c.x + c.w
        return [(px, pb), (px, cy), (end_x, cy)]


@dataclass
class Chart:
    nodes: list
    edges: list
    width: float
    height: float
    trail: list = field(default_factory=list)  # office tree: (name, url) from the top down to the head
    legend: list = field(default_factory=list)  # (label, style) drawn with the chart
    parts: int = 1  # structure chart: how many slides its divisions take in a PDF or PowerPoint
    part_label: str = ""  # one of those slides: "Part 1 of 2 · Divisions 1-3"


# --- Text ---------------------------------------------------------------------------


def _fit(text, font, size, max_width):
    if stringWidth(text, font, size) <= max_width:
        return text
    while text and stringWidth(text.rstrip() + "…", font, size) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(text, font, size, max_width, max_lines):
    """Word-wraps text to max_width (Helvetica metrics), ending with … when it does not fit."""
    lines, current = [], ""
    for word in (text or "").split(" "):  # a no-break space keeps words together
        if not word:
            continue
        trial = f"{current} {word}".strip()
        if not current or stringWidth(trial, font, size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] += "…"
    return [_fit(line, font, size, max_width) for line in lines]


def text_specs(node):
    """The box's text as (text, font, size, colour, max lines), empty parts left out."""
    specs = []
    if node.badge:
        specs.append((node.badge.upper(), BOLD, 8, HIGHLIGHT_BORDER, 1))
    if node.tag:
        specs.append((node.tag.upper(), BOLD, 7.5, MUTED, 1))
    big = HEAD_TEXT if node.emphasis else 1
    if node.flat:
        size = FLAT_HEAD_TEXT if node.emphasis else FLAT_TEXT.get(node.style, 8.5)
        specs += [
            (node.title, BOLD, size, INK, 3),
            (node.subtitle, BOLD if node.emphasis else REGULAR, size * 0.9, INK, 1),
        ]
    else:
        specs += [(node.title, BOLD, 11 * big, INK, 2), (node.subtitle, REGULAR, 9.5 * big, INK, 2)]
    specs += [(node.detail, REGULAR, 8.5 * big, MUTED, 1), (node.more, BOLD, 8.5, node.accent, 1)]
    return [(text, font, size, node.ink(colour), n) for text, font, size, colour, n in specs if text]


def _prepare_text(node):
    pad_x, pad_y = (FLAT_PAD_X, FLAT_PAD_Y) if node.flat else (PAD_X, PAD_Y)
    cursor = pad_y if node.flat else ACCENT + pad_y
    node.lines = []
    for text, font, size, colour, max_lines in text_specs(node):
        # Structure boxes wrap a little short, so the PNG's wider font still fits without shrinking.
        for line in wrap(text, font, size, (node.w - 2 * pad_x) * (0.9 if node.flat else 1), max_lines):
            node.lines.append((line, font, size, colour, cursor + size))
            cursor += size * LINE_SPACING
    node._text_h = cursor + pad_y
    is_position = node.flat and node.style not in (OrgUnit.Kind.DIVISION, OrgUnit.Kind.SECTION)
    node.h = max(node._text_h, POS_MIN_H if is_position else 0)


# --- Layout -------------------------------------------------------------------------


def _hangs(node):
    # Structure charts keep every row a row, as on HRMO's chart.
    return (
        not node.grid and not node.flat and len(node.children) >= STACK_MIN
        and all(not c.children for c in node.children)
    )


def _grid_rows(node):
    """True when the children go in rows of GRID_SIDE either side of the trunk (office tree)."""
    kids = node.children
    return node.grid and len(kids) > GRID_ROW_MAX and all(not c.children and not c.side for c in kids)


def _split_side(node):
    """Coterminous boxes as (left column, right column); a single one goes on the right."""
    half = len(node.side) // 2
    return node.side[:half], node.side[half:]


def _measure_side(node):
    """How far the box and its coterminous boxes reach left and right of the box centre."""
    if not node.side:
        return node.w / 2, node.w / 2
    left, right = _split_side(node)
    if node.side_row:
        reaches = [
            node.w / 2 + COTERM_GAP + sum(c.w for c in column) + SIDE_ROW_GAP * (len(column) - 1)
            for column in (left, right)
        ]
        return tuple(reach if column else node.w / 2 for reach, column in zip(reaches, (left, right)))
    reach = node.w / 2 + COTERM_GAP + BOX_W
    return (reach if left else node.w / 2), (reach if right else node.w / 2)


def _row_width(row):
    return sum(c._width for c in row) + H_GAP * (len(row) - 1) if row else 0


def _halves(row):
    """A row of subtrees split where its two sides are closest in width: (left, right) of the trunk."""
    cut = min(range(len(row) + 1), key=lambda i: abs(_row_width(row[:i]) - _row_width(row[i:])))
    return row[:cut], row[cut:]


def _wrap_rows(node, need_left, need_right):
    """rows: tries every number of children per row and keeps the one that shows the whole chart
    largest on a FIT_W x FIT_H screen. Each row but the last is split either side of the trunk; the
    last is centred on it, where the trunk ends. False when one row is best (laid out as usual)."""
    kids = node.children
    tallest = max(c.h for c in kids)
    for child in kids:
        child.h = tallest
    depth = {}
    for child in kids:  # a trial placement, only to learn how far down each subtree goes
        _place(child, 0, 0, [])
        depth[id(child)] = _bottom(child)
    side = SIDE_DROP + max(c.h for c in node.side) + ROW_GAP if node.side and node.side_row else 0
    best = None
    for per_row in range(1, len(kids) + 1):
        rows = [kids[i : i + per_row] for i in range(0, len(kids), per_row)]
        split = [_halves(row) for row in rows[:-1]]
        last = _row_width(rows[-1]) / 2
        left = max([need_left, last] + [_row_width(a) + TRUNK_GAP / 2 for a, _b in split])
        right = max([need_right, last] + [_row_width(b) + TRUNK_GAP / 2 for _a, b in split])
        height = node.h + side + sum(max(depth[id(c)] for c in row) + 2 * node.vgap for row in rows)
        size = min(FIT_W / (left + right), FIT_H / height)
        if best is None or size > best[0]:
            best = (size, rows, split, left, right)
    _size, rows, split, left, right = best
    if len(rows) == 1:
        return False
    node._rows, node._split = rows, split
    node._anchor, node._width = left, left + right
    return True


def _shift(node, d):
    """Moves the box and everything under it d to the right inside its subtree."""
    node._anchor += d
    node._width += d
    node._offsets = [o + d for o in getattr(node, "_offsets", [])]
    node._joined_offsets = [o + d for o in getattr(node, "_joined_offsets", [])]


def _measure_joined(node):
    """The joined boxes go in a row centred under the box, below its row of children."""
    for child in node.joined:
        _measure(child)
    tallest = max(c.h for c in node.joined)
    for child in node.joined:
        child.h = tallest
    width = sum(c._width for c in node.joined) + H_GAP * (len(node.joined) - 1)
    start = node._anchor - width / 2
    if start < 0:
        _shift(node, -start)
        start = 0
    node._joined_offsets = []
    for child in node.joined:
        node._joined_offsets.append(start)
        start += child._width + H_GAP
    node._width = max(node._width, start - H_GAP)


def _measure(node):
    """Sets node._width (whole subtree) and node._anchor (box centre, from the subtree's left)."""
    if node.joined and not node.children:  # nothing to join: they are just the row under the box
        node.children, node.joined = node.joined, []
    need_left, need_right = _measure_side(node)
    if not node.children:
        node._anchor = need_left
        node._width = need_left + need_right
        return
    for child in node.children:
        _measure(child)
    if _grid_rows(node):
        tallest = max(c.h for c in node.children)
        for child in node.children:
            child.h = tallest  # one height for the whole grid
        half = GRID_SIDE * BOX_W + (GRID_SIDE - 1) * H_GAP + TRUNK_GAP / 2
        node._anchor = max(half, need_left)
        node._width = node._anchor + max(half, need_right)
        return
    node._rows = None
    if node.rows and _wrap_rows(node, need_left, need_right):
        return
    if _hangs(node):
        if len(node.children) >= TWO_SIDED_MIN:
            node._width, node._anchor = 2 * (SIDE_GAP + BOX_W), SIDE_GAP + BOX_W
        else:
            node._width, node._anchor = BOX_W / 2 + SIDE_GAP + BOX_W, BOX_W / 2
        shift = max(0, need_left - node._anchor)
        node._anchor += shift
        node._width = max(node._width + shift, node._anchor + need_right)
        return
    tallest = max(c.h for c in node.children)
    for child in node.children:
        child.h = tallest  # a row of boxes shares one height
    offsets, pos = [], 0
    for child in node.children:
        offsets.append(pos)
        pos += child._width + H_GAP
    centre = (offsets[0] + node.children[0]._anchor + offsets[-1] + node.children[-1]._anchor) / 2
    shift = max(0, need_left - centre)
    node._offsets = [o + shift for o in offsets]
    node._anchor = centre + shift
    node._width = max(pos - H_GAP + shift, node._anchor + need_right)
    if node.joined:
        _measure_joined(node)
    if node.span:  # as wide as everything under it, text wrapped again to the new width
        if node._width < node.w:
            _shift(node, (node.w - node._width) / 2)
        node._width = max(node._width, node.w)
        node.w, node._anchor = node._width, node._width / 2
        _prepare_text(node)


def _place_side(node, edges):
    """Places the coterminous boxes beside the box. Done here, not in _measure, because a row of
    boxes only gets its shared height after it is measured. Returns how far they run below the
    box, so the children can start under them.

    side_row: in a row each side, centred on one line just below the box and joined to the trunk
    (HRMO's charts). Otherwise stacked in a column each side, the first level with the box."""
    if not node.side:
        return 0
    left, right = _split_side(node)
    if node.side_row:
        tallest = max(c.h for c in node.side)
        cy = node.y + node.h + SIDE_DROP + tallest / 2
        for column, kind in ((left[::-1], "left"), (right, "right")):  # from the trunk outwards
            x, inner = (node.x - COTERM_GAP if kind == "left" else node.x + node.w + COTERM_GAP), node
            for child in column:
                child.x, child.y = (x - child.w if kind == "left" else x), cy - child.h / 2
                x = child.x - SIDE_ROW_GAP if kind == "left" else child.x + child.w + SIDE_ROW_GAP
                edges.append(Edge(inner, child, kind if inner is node else f"side-{kind}"))
                inner = child
        return SIDE_DROP + tallest + ROW_GAP
    firsts = [column[0].h for column in (left, right) if column]
    if node.emphasis and max(firsts) > node.h:
        # The head has no row to match: grow it, text centred, so the first side boxes line up.
        grow = (max(firsts) - node.h) / 2
        node.lines = [(text, font, size, colour, dy + grow) for text, font, size, colour, dy in node.lines]
        node.h = max(firsts)
    bottom = node.y + node.h
    for column, side in ((left, "side-left"), (right, "side-right")):
        # The first box centred on the box (a straight line), but clear of the line above it.
        y = node.y - min((column[0].h - node.h) / 2, V_GAP / 3) if column else 0
        for child in column:
            child.x = node.x + node.w + COTERM_GAP if side == "side-right" else node.x - COTERM_GAP - BOX_W
            child.y = y
            edges.append(Edge(node, child, side))
            bottom = max(bottom, y + child.h)
            y += child.h + ROW_GAP
    return bottom - (node.y + node.h) + (ROW_GAP if bottom > node.y + node.h else 0)


def _bottom(node):
    return max([node.y + node.h] + [_bottom(c) for c in node.children + node.joined])


def _column_end(node):
    """The last box down a column of single boxes: where a joined box's line starts."""
    while len(node.children) == 1 and not node.joined:
        node = node.children[0]
    return node


def _place(node, left, top, edges):
    node.x, node.y = left + node._anchor - node.w / 2, top
    trunk = left + node._anchor
    top += _place_side(node, edges)  # the children start below the coterminous boxes
    if not node.children:
        return
    if _grid_rows(node):
        # The trunk runs down the gap between the middle columns; each row has its own line
        # across, just above it. The trunk ends at the last row, so that row is centred on it.
        per_row, y = 2 * GRID_SIDE, top + node.h + V_GAP
        for start in range(0, len(node.children), per_row):
            row = node.children[start : start + per_row]
            last = start + per_row >= len(node.children)
            for i, child in enumerate(row):
                if last:
                    x = trunk - (len(row) * (BOX_W + H_GAP) - H_GAP) / 2 + i * (BOX_W + H_GAP)
                elif i < GRID_SIDE:
                    x = trunk - TRUNK_GAP / 2 - (GRID_SIDE - i) * (BOX_W + H_GAP) + H_GAP
                else:
                    x = trunk + TRUNK_GAP / 2 + (i - GRID_SIDE) * (BOX_W + H_GAP)
                _place(child, x, y, edges)
                edges.append(Edge(node, child, "below"))
            y += row[0].h + V_GAP
        return
    if getattr(node, "_rows", None):
        # Each row's line across sits just above it; the trunk runs down the gap in the split rows.
        y = top + node.h + node.vgap
        for i, row in enumerate(node._rows):
            if i < len(node._split):
                left_half, right_half = node._split[i]
                starts = [trunk - TRUNK_GAP / 2 - _row_width(left_half), trunk + TRUNK_GAP / 2]
                halves = [left_half, right_half]
            else:
                starts, halves = [trunk - _row_width(row) / 2], [row]
            for x, half in zip(starts, halves):
                for child in half:
                    _place(child, x, y, edges)
                    edges.append(Edge(node, child, "below"))
                    x += child._width + H_GAP
            y = max(_bottom(c) for c in row) + 2 * node.vgap
        return
    if not _hangs(node):
        for child, offset in zip(node.children, node._offsets):
            _place(child, left + offset, top + node.h + node.vgap, edges)
            edges.append(Edge(node, child, "below"))
        if node.joined:
            y = max(_bottom(c) for c in node.children) + node.vgap
            ends = [_column_end(c) for c in node.children]
            for child, offset in zip(node.joined, node._joined_offsets):
                _place(child, left + offset, y, edges)
                edges += [Edge(end, child, "below") for end in ends]
        return
    kids = node.children
    if len(kids) >= TWO_SIDED_MIN:
        half = math.ceil(len(kids) / 2)
        columns = [(kids[:half], "left"), (kids[half:], "right")]
    else:
        columns = [(kids, "right")]
    for column, side in columns:
        y = top + node.h + ROW_GAP * 1.5
        for child in column:
            x = trunk + SIDE_GAP if side == "right" else trunk - SIDE_GAP - BOX_W
            _place(child, x, y, edges)
            edges.append(Edge(node, child, side))
            y += child.h + ROW_GAP


def layout(roots):
    """Places a forest of ChartNodes side by side and returns the Chart."""
    nodes = []

    def walk(node):
        nodes.append(node)
        _prepare_text(node)
        for child in node.side + node.children + node.joined:
            walk(child)

    for root in roots:
        walk(root)
    if roots:
        tallest = max(r.h for r in roots)
        for root in roots:
            root.h = tallest
    edges, left = [], MARGIN
    for root in roots:
        _measure(root)
        _place(root, left, MARGIN, edges)
        left += root._width + H_GAP * 2
    for node in nodes:  # structure boxes: text centred in a box made taller by its row
        if node.flat and node.h > node._text_h:
            down = (node.h - node._text_h) / 2
            node.lines = [(text, font, size, colour, dy + down) for text, font, size, colour, dy in node.lines]
    width = max((n.x + n.w for n in nodes), default=0) + MARGIN
    height = max((n.y + n.h for n in nodes), default=0) + MARGIN
    return Chart(nodes, edges, width, height)


# --- Building charts from records ---------------------------------------------------


def chart_name(person):
    middle = f" {person.middle_name[0]}." if person.middle_name else ""
    suffix = f" {person.suffix}" if person.suffix else ""
    return f"{person.first_name}{middle} {person.last_name}{suffix}"


def sort_key(eng):
    return (TYPE_ORDER.get(eng.type, 99), eng.position_title.lower(), eng.person.last_name, eng.person.first_name)


def _reaches(start_pk, target_pk, parent_of):
    seen = set()
    while start_pk is not None and start_pk not in seen:
        if start_pk == target_pk:
            return True
        seen.add(start_pk)
        start_pk = parent_of.get(start_pk)
    return False


@dataclass
class StaffChart:
    chart: Chart
    on_chart: list  # active chart-type engagements, sorted
    roots: list  # engagements at the top of the chart
    head: Engagement = None  # the single top engagement, if there is exactly one
    not_drawn: dict = field(default_factory=dict)  # type label -> active count (JO, COS, project-based)
    broken_lines: list = field(default_factory=list)  # engagements whose supervisor is no longer on the chart


def build_staff_chart(office, show_types=False, linked_person_ids=(), you=None):
    """The office's staff chart: active Elected, Plantilla, Casual and Contractual appointments.

    you: the viewer's own active engagement in this office (badged "You", and their
    supervisor "Your supervisor"); linked_person_ids: people whose names may link to their record.
    """
    active = list(
        office.engagements.filter(status=Engagement.Status.ACTIVE).select_related("person")
    )
    drawn = sorted((e for e in active if e.on_chart), key=sort_key)
    by_pk = {e.pk: e for e in drawn}
    nodes = {}
    supervisor_pk = you.reports_to_id if you else None
    for eng in drawn:
        badge = "You" if you and eng.pk == you.pk else ("Your supervisor" if eng.pk == supervisor_pk else "")
        nodes[eng.pk] = ChartNode(
            title=chart_name(eng.person),
            subtitle=eng.position_title,
            detail=eng.get_type_display() if show_types else "",
            style=eng.type,
            url=reverse("person_detail", args=[eng.person_id]) if eng.person_id in linked_person_ids else "",
            badge=badge,
        )

    parent_of = {e.pk: e.reports_to_id for e in drawn if e.reports_to_id in by_pk}
    roots, broken = [], []
    for eng in drawn:
        parent = parent_of.get(eng.pk)
        # A line to someone no longer on the chart, or one that loops back, starts a new top box.
        if parent is not None and parent != eng.pk and not _reaches(parent, eng.pk, parent_of):
            nodes[parent].children.append(nodes[eng.pk])
        else:
            parent_of.pop(eng.pk, None)
            roots.append(eng)
            if eng.reports_to_id:
                broken.append(eng)

    not_drawn = {}
    for eng in active:
        if not eng.on_chart:
            label = eng.get_type_display()
            not_drawn[label] = not_drawn.get(label, 0) + 1

    return StaffChart(
        chart=layout([nodes[e.pk] for e in roots]),
        on_chart=drawn,
        roots=roots,
        head=roots[0] if len(roots) == 1 else None,
        not_drawn=not_drawn,
        broken_lines=broken,
    )


def office_heads(office_ids):
    """office pk -> the single top engagement of its staff chart (offices with exactly one)."""
    tops = {}
    active = Engagement.objects.filter(
        office_id__in=office_ids, status=Engagement.Status.ACTIVE, type__in=Engagement.CHART_TYPES
    ).select_related("person")
    on_chart = {e.pk for e in active}
    for eng in active:
        if eng.reports_to_id not in on_chart:
            tops.setdefault(eng.office_id, []).append(eng)
    return {office_id: engs[0] for office_id, engs in tops.items() if len(engs) == 1}


def tree_url(focus=None, whole=False):
    """The office tree page: with an office as its head, the whole tree, or the top level."""
    url = reverse("office_tree")
    if whole:
        return url + "?all=1"
    return url + (f"?office={focus}" if focus else "")


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _position_node(position, width=POS_W):
    return ChartNode(
        title=position.title,
        subtitle=f"SG {position.salary_grade}" if position.salary_grade else "",
        style=position.status,
        dashed=position.coterminous,
        flat=True,
        w=width,
    )


def _unit_node(unit, units, positions):
    """A division or section box with what is under it, in the order HRMO set: each position under
    the one it reports to, and the sections under the unit's top position when it has just one."""
    beside = unit.beside_head
    node = ChartNode(
        title=f"{unit.name} ({unit.acronym})" if unit.acronym else unit.name,
        style=unit.kind,
        dashed=beside,
        flat=True,
        span=not beside,
        w=SIDE_W if beside else POS_W,
    )
    mine = [p for p in positions if p.unit_id == unit.pk]
    boxes = {p.pk: _position_node(p) for p in mine}
    upper = {p.pk: p.reports_to_id for p in mine if p.reports_to_id in boxes}
    for position in mine:
        parent = upper.get(position.pk)
        # A line to a position outside the unit, or one that loops back, starts at the unit box.
        if parent is not None and parent != position.pk and not _reaches(parent, position.pk, upper):
            target = boxes[parent]
        else:
            upper.pop(position.pk, None)
            target = node
        (target.joined if position.joins_row else target.children).append(boxes[position.pk])
    sections = [_unit_node(u, units, positions) for u in units if u.parent_id == unit.pk]
    (node.children[0] if len(node.children) == 1 else node).children.extend(sections)
    if unit.kind == OrgUnit.Kind.SECTION and not node.children and not node.joined:
        node.children = [ChartNode(title="Positions to follow", style=PLACEHOLDER, flat=True, w=POS_W)]
    return node


def build_structure(office, base):
    """The office's own structure chart (DECISIONS O7): its head position at the top, the
    positions and bodies beside the head in a row just below it, and each division with its
    top position, its sections and their positions in columns under it. base: the office's
    box on the office tree (its detail line and link)."""
    units = list(office.units.all())
    positions = list(office.structure_positions.all())
    head_position = next((p for p in positions if p.unit_id is None and not p.beside_head), None)
    head = ChartNode(
        title=head_position.title if head_position else office.name,
        subtitle=f"SG {head_position.salary_grade}" if head_position and head_position.salary_grade else office.head_title,
        detail=base.detail,
        style=head_position.status if head_position else office.branch,
        url=base.url,
        flat=True,
        side_row=True,
        w=FLAT_HEAD_W,
    )
    side = [(p.order, _position_node(p, SIDE_W)) for p in positions if p.unit_id is None and p.beside_head]
    side += [(u.order, _unit_node(u, units, positions)) for u in units if u.parent_id is None and u.beside_head]
    head.side = [n for _order, n in sorted(side, key=lambda item: item[0])]
    for unit in sorted((u for u in units if u.parent_id is None and not u.beside_head), key=lambda u: u.order):
        head.children.append(_unit_node(unit, units, positions))
    return head


def _split_divisions(head, office, part):
    """Keeps only the divisions of one slide (part, from 1) under the structure head, with a
    dashed box naming the divisions on the other slides. Returns (parts, label)."""
    divisions = head.children
    parts = math.ceil(len(divisions) / DIVISIONS_PER_SLIDE)
    if not part or parts < 2:
        return parts, ""
    part = min(max(part, 1), parts)
    names = [
        u.acronym or u.name
        for u in sorted(
            office.units.filter(parent=None, beside_head=False, kind=OrgUnit.Kind.DIVISION), key=lambda u: u.order
        )
    ]
    start, end = (part - 1) * DIVISIONS_PER_SLIDE, part * DIVISIONS_PER_SLIDE
    head.children = divisions[start:end]

    def pointer(title, others):
        return ChartNode(title=title, subtitle=" · ".join(others), style=PLACEHOLDER, flat=True, dashed=True, w=POS_W)

    if part > 1:
        head.children.insert(0, pointer("Continued from previous slide", names[:start]))
    if part < parts:
        head.children.append(pointer("Continued on next slide", names[end:]))
    shown = min(end, len(divisions))
    label = f"Part {part} of {parts} · Divisions {start + 1}–{shown}" if shown > start + 1 else f"Part {part} of {parts} · Division {shown}"
    return parts, label


def build_office_tree(detail_office_ids=(), linked_office_ids=(), focus=None, whole=False, part=None):
    """The municipal office tree. Head names and staff counts only for detail_office_ids.

    focus: pk of an office to draw as the head at the top, with its divisions under it and its
    coterminous offices beside it (DECISIONS O6). Without it, the municipality is the head with
    the top offices under it. part: only that slide's divisions of a structure chart (exports).
    Offices one level further down are folded: their box says what
    is under them and clicking it makes them the head. whole: nothing folded.
    """
    offices = list(Office.objects.select_related("parent").order_by("name"))
    by_pk = {o.pk: o for o in offices}
    detail_ids = set(detail_office_ids)
    heads = office_heads(detail_ids)
    counts = dict(
        Office.objects.filter(pk__in=detail_ids)
        .annotate(n=Count("engagements", filter=Q(engagements__status=Engagement.Status.ACTIVE)))
        .values_list("pk", "n")
    )
    nodes = {}
    for office in offices:
        detail = ""
        if office.pk in detail_ids:
            head = heads.get(office.pk)
            staff = f"{counts.get(office.pk, 0)} staff"
            detail = f"{chart_name(head.person)} · {staff}" if head else staff
        nodes[office.pk] = ChartNode(
            title=office.name,
            subtitle=office.head_title,
            detail=detail,
            style=office.branch,
            url=reverse("office_chart", args=[office.pk]) if office.pk in linked_office_ids else "",
            grid=True,
        )
    has_children = {o.parent_id for o in offices if o.parent_id in nodes and o.parent_id != o.pk}
    divisions = dict(
        OrgUnit.objects.filter(parent=None, beside_head=False, kind=OrgUnit.Kind.DIVISION)
        .values_list("office").annotate(n=Count("pk"))
    )
    has_structure = divisions.keys() | set(StructurePosition.objects.values_list("office_id", flat=True))
    roots = []
    for office in offices:
        if office.parent_id in nodes and office.parent_id != office.pk:
            # A coterminous office with sub-offices of its own needs the room below it.
            beside = office.coterminous and office.pk not in has_children
            parent = nodes[office.parent_id]
            (parent.side if beside else parent.children).append(nodes[office.pk])
            if beside:
                nodes[office.pk].tag, nodes[office.pk].dashed = "Coterminous", True
        else:
            roots.append(nodes[office.pk])

    trail = [("Municipal Government of San Pascual", tree_url())]
    legend, slides, part_label = [], 1, ""
    if focus in nodes:
        head = nodes[focus]
        if focus in has_structure:  # its own chart, with its sub-offices still beside or under it
            office_node = head
            head = build_structure(by_pk[focus], office_node)
            slides, part_label = _split_divisions(head, by_pk[focus], part)
            head.side += office_node.side
            if not part or part >= slides:  # sub-offices go after the last division
                head.children += office_node.children
            head.rows = not part  # the whole chart (page, PNG) wraps its divisions into rows
            legend = STRUCTURE_LEGEND
        chain, current = [], by_pk[focus]
        while current is not None and current.pk not in {o.pk for o in chain}:
            chain.insert(0, current)
            current = current.parent
        trail += [(o.name, tree_url(o.pk)) for o in chain]
    else:
        # Departments first, then the legislative side, then the rest (stable by name).
        # Offices with more under them lead their branch.
        order = {Office.Branch.EXECUTIVE: 0, Office.Branch.LEGISLATIVE: 1}
        opens = {id(nodes[pk]) for pk in has_children | has_structure}
        roots.sort(key=lambda n: (order.get(n.style, 2), id(n) not in opens, n.title))
        if len(roots) == 1:
            head = roots[0]
        else:
            head = ChartNode(
                title="Municipal Government of San Pascual", subtitle="Local Government Unit", children=roots, grid=True
            )
    head.emphasis = True
    if not head.flat:  # a structure head keeps its own size
        head.w = HEAD_W

    # An office box with more under it says what, and clicking it makes it the head. Its own
    # structure is only ever drawn on its own chart; folded sub-offices only when not whole.
    for pk, node in nodes.items():
        if node is head or pk == focus:
            continue
        folding = not whole and (node.children or node.side)
        if pk not in has_structure and not folding:
            continue
        n_below = divisions.get(pk, 0) + (len(node.children) if folding else 0)
        parts = [_plural(n_below, "division")] if n_below else []
        parts += [f"{len(node.side)} coterminous"] if folding and node.side else []
        node.more = "+ " + ", ".join(parts or ["structure chart"])
        node.url = tree_url(pk)
        if folding:
            node.children, node.side = [], []
    chart = layout([head])
    chart.trail, chart.legend = trail, legend
    chart.parts, chart.part_label = slides, part_label
    return chart


# --- Reporting-line rules (DECISIONS O2) ---------------------------------------------

CHART_TYPES_TEXT = "an active Elected, Plantilla, Casual or Contractual appointment"


def supervisor_error(engagement_pk, office, supervisor):
    """Why `supervisor` cannot be the reporting line of engagement `engagement_pk` in `office`."""
    if supervisor is None:
        return None
    if engagement_pk and supervisor.pk == engagement_pk:
        return "An appointment cannot report to itself."
    if office is not None and supervisor.office_id != office.pk:
        return "Choose someone in the same office. Offices are linked to each other in the office tree."
    if supervisor.status != Engagement.Status.ACTIVE or not supervisor.on_chart:
        return f"Choose {CHART_TYPES_TEXT}."
    seen, current = set(), supervisor
    while current is not None and current.pk not in seen:
        if engagement_pk and current.reports_to_id == engagement_pk:
            return "That would make a circular reporting line."
        seen.add(current.pk)
        current = current.reports_to
    return None


def reporting_line_errors(by_pk, proposed):
    """Checks a whole office's proposed lines at once. by_pk: active engagements of the office;
    proposed: engagement pk -> supervisor pk or None. Returns pk -> message."""
    errors = {}
    for pk, sup in proposed.items():
        if sup is None:
            continue
        if sup == pk:
            errors[pk] = "An appointment cannot report to itself."
        elif sup not in by_pk or not by_pk[sup].on_chart:
            errors[pk] = f"Choose {CHART_TYPES_TEXT} in this office."
    for pk, sup in proposed.items():
        if pk not in errors and sup is not None and _reaches(sup, pk, proposed):
            errors[pk] = "That would make a circular reporting line."
    return errors


def hand_over_direct_reports(old):
    """When an appointment stops being active, moves its active direct reports to the same person's
    other active chart appointment in that office (a renewal or promotion), if there is exactly one.

    Each move is a normal save with a version bump, so it is audit-logged. Returns how many moved.
    """
    if old.status == Engagement.Status.ACTIVE:
        return 0
    successors = list(
        Engagement.objects.filter(
            person_id=old.person_id,
            office_id=old.office_id,
            status=Engagement.Status.ACTIVE,
            type__in=Engagement.CHART_TYPES,
        ).exclude(pk=old.pk)
    )
    if len(successors) != 1:
        return 0
    new = successors[0]
    moved = 0
    for report in Engagement.objects.select_for_update().filter(reports_to=old, status=Engagement.Status.ACTIVE).exclude(pk=new.pk):
        report.reports_to = new
        report.version += 1
        report.save()
        moved += 1
    return moved


# --- Renderers ----------------------------------------------------------------------


def render_svg(chart, label=""):
    """Inline SVG for the page. Text comes from the database, so everything is escaped."""
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {chart.width:.0f} {chart.height:.0f}" '
        f'width="{chart.width:.0f}" height="{chart.height:.0f}" role="img" aria-label="{escape(label)}" '
        'font-family="Helvetica, Arial, sans-serif" class="org-chart">'
    ]
    for edge in chart.edges:
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in edge.points)
        dash = f' stroke-dasharray="{DASH[0]} {DASH[1]}"' if edge.dashed else ""
        out.append(f'<polyline points="{points}" fill="none" stroke="{edge.colour}" stroke-width="1.5"{dash}/>')
    for node in chart.nodes:
        if node.url:
            out.append(f'<a href="{escape(node.url)}">')
        out.append("<g>")
        out.append(f"<title>{escape(' · '.join(t for t in (node.title, node.subtitle, node.detail) if t))}</title>")
        width = 2.5 if node.badge else 1.5
        dash = f' stroke-dasharray="{DASH[0]} {DASH[1]}"' if node.outline_dashed else ""
        out.append(
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.w:.1f}" height="{node.h:.1f}" rx="{node.radius}" '
            f'fill="{node.fill}" stroke="{node.accent}" stroke-width="{width}"{dash}/>'
        )
        if node.band:
            out.append(f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.w:.1f}" height="{ACCENT}" fill="{node.accent}"/>')
        cx = node.x + node.w / 2
        for text, font, size, colour, dy in node.lines:
            weight = ' font-weight="bold"' if font == BOLD else ""
            out.append(
                f'<text x="{cx:.1f}" y="{node.y + dy:.1f}" font-size="{size}" fill="{colour}" '
                f'text-anchor="middle"{weight}>{escape(text)}</text>'
            )
        out.append("</g>")
        if node.url:
            out.append("</a>")
    out.append("</svg>")
    return "".join(out)


HEADING_SPACE = 44  # room above the chart for the heading and note in exported files
LEGEND_SIZE, LEGEND_KEY = 8, 9  # legend text size and colour key square, in exported files


def legend_items(chart, right):
    """The chart's legend laid out in one line ending at x = right: (x, label, accent, fill)."""
    items, x = [], right
    for label, style in reversed(chart.legend):
        x -= stringWidth(label, REGULAR, LEGEND_SIZE)
        items.insert(0, (x - LEGEND_KEY - 4, label, STYLES[style], FILLS.get(style, "#ffffff")))
        x -= LEGEND_KEY + 4 + 14
    return items


def _hex(colour):
    colour = colour.lstrip("#")
    return tuple(int(colour[i : i + 2], 16) for i in (0, 2, 4))


def _charts(chart):
    return chart if isinstance(chart, list) else [chart]


def _page_heading(heading, chart):
    return f"{heading} · {chart.part_label}" if chart.part_label else heading


def render_pdf(chart, heading, note=""):
    """Vector PDF, a page per chart (a list of charts: a structure chart split by slide).
    Each page is A4 landscape, then A3, at no less than 60% size; otherwise as large as the
    chart (it still prints with "fit to page")."""
    from reportlab.lib.pagesizes import A3, A4, landscape
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.setTitle(heading)
    pdf.setAuthor("San Pascual HR")
    for chart in _charts(chart):
        margin = 28
        need_w, need_h = chart.width, chart.height + HEADING_SPACE
        for page in (landscape(A4), landscape(A3)):
            scale = min(1, (page[0] - 2 * margin) / need_w, (page[1] - 2 * margin) / need_h)
            if scale >= 0.6:
                break
        else:
            page, scale = (need_w + 2 * margin, need_h + 2 * margin), 1
        page_w, page_h = page
        left = (page_w - need_w * scale) / 2

        def pt(x, y):  # chart coordinates (y down) -> PDF points (y up)
            return left + x * scale, page_h - margin - (HEADING_SPACE + y) * scale

        pdf.setPageSize(page)
        pdf.setFont(BOLD, 14)
        pdf.setFillColorRGB(*[c / 255 for c in _hex(INK)])
        pdf.drawString(margin, page_h - margin - 12, _page_heading(heading, chart))
        if note:
            pdf.setFont(REGULAR, 9)
            pdf.setFillColorRGB(*[c / 255 for c in _hex(MUTED)])
            pdf.drawString(margin, page_h - margin - 28, note)
        pdf.setFont(REGULAR, LEGEND_SIZE)
        for x, label, accent, fill in legend_items(chart, page_w - margin):
            pdf.setStrokeColorRGB(*[c / 255 for c in _hex(accent)])
            pdf.setFillColorRGB(*[c / 255 for c in _hex(fill)])
            pdf.setLineWidth(1.5)
            pdf.rect(x, page_h - margin - 12, LEGEND_KEY, LEGEND_KEY, stroke=1, fill=1)
            pdf.setFillColorRGB(*[c / 255 for c in _hex(INK)])
            pdf.drawString(x + LEGEND_KEY + 4, page_h - margin - 11, label)

        pdf.setLineWidth(1.5 * scale)
        for edge in chart.edges:
            pdf.setStrokeColorRGB(*[c / 255 for c in _hex(edge.colour)])
            path = pdf.beginPath()
            points = [pt(x, y) for x, y in edge.points]
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            pdf.setDash([d * scale for d in DASH] if edge.dashed else [])
            pdf.drawPath(path, stroke=1, fill=0)
        pdf.setDash([])

        for node in chart.nodes:
            x, y_top = pt(node.x, node.y)
            w, h = node.w * scale, node.h * scale
            pdf.setLineWidth((2.5 if node.badge else 1.5) * scale)
            pdf.setStrokeColorRGB(*[c / 255 for c in _hex(node.accent)])
            pdf.setFillColorRGB(*[c / 255 for c in _hex(node.fill)])
            pdf.setDash([d * scale for d in DASH] if node.outline_dashed else [])
            pdf.roundRect(x, y_top - h, w, h, node.radius * scale, stroke=1, fill=1)
            pdf.setDash([])
            if node.band:
                pdf.setFillColorRGB(*[c / 255 for c in _hex(node.accent)])
                pdf.rect(x, y_top - ACCENT * scale, w, ACCENT * scale, stroke=0, fill=1)
            for text, font, size, colour, dy in node.lines:
                pdf.setFillColorRGB(*[c / 255 for c in _hex(colour)])
                pdf.setFont(font, size * scale)
                pdf.drawCentredString(x + w / 2, y_top - dy * scale, text)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def _png_font(font, size_px, _cache={}):
    from PIL import ImageFont
    import reportlab

    key = (font, round(size_px, 1))
    if key not in _cache:
        name = "VeraBd.ttf" if font == BOLD else "Vera.ttf"  # Bitstream Vera, shipped with reportlab
        path = os.path.join(os.path.dirname(reportlab.__file__), "fonts", name)
        _cache[key] = ImageFont.truetype(path, size_px)
    return _cache[key]


def render_png(chart, heading, note="", scale=2):
    """PNG at 2x for sharp printing and slides. Lines wider in Vera than in Helvetica are
    shrunk to fit their box."""
    from PIL import Image, ImageDraw

    # A 16:9 frame (never cropped, only padded) so the picture drops straight onto a slide.
    content_h = chart.height + HEADING_SPACE
    frame_w, frame_h = max(chart.width, content_h * 16 / 9), max(content_h, chart.width * 9 / 16)
    off_x, off_y = (frame_w - chart.width) / 2, (frame_h - content_h) / 2
    width, height = int(frame_w * scale), int(frame_h * scale)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    draw.text((MARGIN * scale, 10 * scale), heading, font=_png_font(BOLD, 16 * scale), fill=INK)
    if note:
        draw.text((MARGIN * scale, 30 * scale), note, font=_png_font(REGULAR, 9 * scale), fill=MUTED)
    for x, label, accent, fill in legend_items(chart, frame_w - MARGIN):
        draw.rectangle([x * scale, 13 * scale, (x + LEGEND_KEY) * scale, (13 + LEGEND_KEY) * scale], fill=fill, outline=accent, width=max(1, int(1.5 * scale)))
        draw.text(((x + LEGEND_KEY + 4) * scale, (13 + LEGEND_KEY) * scale), label, font=_png_font(REGULAR, LEGEND_SIZE * scale), fill=INK, anchor="ls")

    def px(x, y):
        return (x + off_x) * scale, (y + HEADING_SPACE + off_y) * scale

    def dashed(points, colour, width):
        on, off = DASH[0] * scale, DASH[1] * scale  # PIL has no dashes: draw them one by one
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            length = math.hypot(x1 - x0, y1 - y0)
            start = 0
            while start < length:
                end = min(start + on, length)
                a, b = start / length, end / length
                draw.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=colour, width=width)
                start += on + off

    line_w = max(1, int(1.5 * scale))
    for edge in chart.edges:
        points = [px(x, y) for x, y in edge.points]
        if edge.dashed:
            dashed(points, edge.colour, line_w)
        else:
            draw.line(points, fill=edge.colour, width=line_w, joint="curve")
    for node in chart.nodes:
        x0, y0 = px(node.x, node.y)
        x1, y1 = px(node.x + node.w, node.y + node.h)
        border = int((2.5 if node.badge else 1.5) * scale)
        if node.outline_dashed:
            draw.rectangle([x0, y0, x1, y1], fill=node.fill)
            dashed([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], node.accent, border)
        else:
            draw.rounded_rectangle([x0, y0, x1, y1], radius=node.radius * scale, fill=node.fill, outline=node.accent, width=border)
        if node.band:
            draw.rectangle([x0, y0, x1, y0 + ACCENT * scale], fill=node.accent)
        max_w = (node.w - 2 * (FLAT_PAD_X if node.flat else PAD_X)) * scale
        for text, font, size, colour, dy in node.lines:
            size_px = size * scale
            fnt = _png_font(font, size_px)
            text_w = fnt.getlength(text)
            if text_w > max_w:
                fnt = _png_font(font, size_px * max_w / text_w)
            draw.text(((x0 + x1) / 2, y0 + dy * scale), text, font=fnt, fill=colour, anchor="ms")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# PowerPoint ---------------------------------------------------------------------------

# Connection sites of a (rounded) rectangle: top, left, bottom, right.
SITE_TOP, SITE_LEFT, SITE_BOTTOM, SITE_RIGHT = 0, 1, 2, 3


def connector_frame(start, end, first_leg=(0, 1)):
    """Rotation and flips that make a bent connector run from start to end.

    An unrotated bentConnector2/3 starts at its frame's top-left, leaves along +x and ends at
    the bottom-right. OOXML flips the frame about its centre, then rotates it clockwise. This
    tries each combination and returns (rot_degrees, flip_h, flip_v, (left, top, width, height)).
    """
    (sx, sy), (ex, ey) = start, end
    cx, cy = (sx + ex) / 2, (sy + ey) / 2
    dx, dy = abs(ex - sx), abs(ey - sy)

    def close(a, b):
        return abs(a - b) < 0.01

    for rot in (0, 90, 180, 270):
        fw, fh = (dx, dy) if rot in (0, 180) else (dy, dx)
        cos, sin = round(math.cos(math.radians(rot))), round(math.sin(math.radians(rot)))
        for flip_h in (False, True):
            for flip_v in (False, True):

                def turn(x, y):
                    x, y = (-x if flip_h else x), (-y if flip_v else y)
                    return x * cos - y * sin, x * sin + y * cos

                s, e, leg = turn(-fw / 2, -fh / 2), turn(fw / 2, fh / 2), turn(1, 0)
                if (
                    close(cx + s[0], sx) and close(cy + s[1], sy)
                    and close(cx + e[0], ex) and close(cy + e[1], ey)
                    and close(leg[0], first_leg[0]) and close(leg[1], first_leg[1])
                ):
                    return rot, flip_h, flip_v, (cx - fw / 2, cy - fh / 2, fw, fh)
    raise ValueError("no connector frame")


def render_pptx(chart, heading, note=""):
    """An editable slide per chart (a list of charts: a structure chart split by slide), all the
    same size: every box is a rounded rectangle and every line a connector glued to
    its two boxes, so moving a box in PowerPoint drags its lines along."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_LINE_DASH_STYLE
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Emu, Pt

    charts = _charts(chart)
    margin, head = 24, HEADING_SPACE + 12  # room for a slide-sized title
    slide_w, slide_h, max_side = 960, 540, 4032  # 16:9 in points; PowerPoint allows up to 56 in
    for chart in charts:
        scale = min(1, (slide_w - 2 * margin) / chart.width, (slide_h - head - margin) / chart.height)
        if scale < 0.5:  # too small to read on 16:9: grow the slide instead
            scale = min(0.5, (max_side - 2 * margin) / chart.width, (max_side - head - margin) / chart.height)
            slide_w = max(slide_w, chart.width * scale + 2 * margin)
            slide_h = max(slide_h, chart.height * scale + head + margin)
    slide_w = max(slide_w, slide_h * 16 / 9)  # a grown slide stays 16:9, so it still presents full screen
    slide_h = max(slide_h, slide_w * 9 / 16)

    def emu(points):
        return Emu(int(round(points * 12700)))

    prs = Presentation()
    prs.slide_width, prs.slide_height = emu(slide_w), emu(slide_h)
    for chart in charts:
        scale = min(1, (slide_w - 2 * margin) / chart.width, (slide_h - head - margin) / chart.height)
        left = (slide_w - chart.width * scale) / 2

        def to_slide(x, y, left=left, scale=scale):
            return left + x * scale, head + y * scale

        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

        legend_w = sum(stringWidth(label, REGULAR, LEGEND_SIZE) + LEGEND_KEY + 18 for label, _style in chart.legend)
        title = slide.shapes.add_textbox(emu(margin), emu(8), emu(slide_w - 2 * margin - legend_w), emu(head - 8))
        title.text_frame.word_wrap = True
        for i, (text, size, bold, colour) in enumerate(((_page_heading(heading, chart), 20, True, INK), (note, 10, False, MUTED))):
            if not text:
                continue
            para = title.text_frame.paragraphs[0] if i == 0 else title.text_frame.add_paragraph()
            run = para.add_run()
            run.text = text
            run.font.size, run.font.bold, run.font.name = Pt(size), bold, "Arial"
            run.font.color.rgb = RGBColor(*_hex(colour))

        for x, label, accent, fill in legend_items(chart, slide_w - margin):
            key = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, emu(x), emu(12), emu(LEGEND_KEY), emu(LEGEND_KEY))
            key.shadow.inherit = False
            key.fill.solid()
            key.fill.fore_color.rgb = RGBColor(*_hex(fill))
            key.line.color.rgb = RGBColor(*_hex(accent))
            key.name = f"Legend: {label}"
            text = slide.shapes.add_textbox(emu(x + LEGEND_KEY + 2), emu(7), emu(stringWidth(label, REGULAR, LEGEND_SIZE) + 12), emu(LEGEND_KEY + 10))
            frame = text.text_frame
            frame.margin_top = frame.margin_bottom = 0
            run = frame.paragraphs[0].add_run()
            run.text = label
            run.font.size, run.font.name = Pt(LEGEND_SIZE), "Arial"
            run.font.color.rgb = RGBColor(*_hex(INK))

        shapes = {}
        for node in chart.nodes:
            x, y = to_slide(node.x, node.y)
            kind = MSO_SHAPE.ROUNDED_RECTANGLE if node.radius else MSO_SHAPE.RECTANGLE
            shape = slide.shapes.add_shape(kind, emu(x), emu(y), emu(node.w * scale), emu(node.h * scale))
            shape.name = node.title[:60]
            if node.radius:
                shape.adjustments[0] = 0.08
            shape.shadow.inherit = False
            shape.fill.solid()
            shape.fill.fore_color.rgb = RGBColor(*_hex(node.fill))
            shape.line.color.rgb = RGBColor(*_hex(node.accent))
            shape.line.width = emu((2.5 if node.badge else 1.5) * scale)
            if node.outline_dashed:
                shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
            frame = shape.text_frame
            frame.word_wrap = True
            frame.vertical_anchor = MSO_ANCHOR.MIDDLE
            frame.margin_left = frame.margin_right = emu(PAD_X * scale / 2)
            frame.margin_top = frame.margin_bottom = emu(PAD_Y * scale / 2)
            # Whole strings, not the wrapped lines: PowerPoint wraps them again as people edit.
            for i, (text, font, size, colour, _max_lines) in enumerate(text_specs(node)):
                para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
                para.alignment = PP_ALIGN.CENTER
                run = para.add_run()
                run.text = text
                run.font.size, run.font.bold, run.font.name = Pt(size * scale), font == BOLD, "Arial"
                run.font.color.rgb = RGBColor(*_hex(colour))
            shapes[id(node)] = shape

        for edge in chart.edges:
            points = [to_slide(x, y) for x, y in edge.points]
            start, end = points[0], points[-1]
            start_site, end_site = {
                "below": (SITE_BOTTOM, SITE_TOP),
                "right": (SITE_BOTTOM, SITE_LEFT),
                "left": (SITE_BOTTOM, SITE_RIGHT),
                "side-right": (SITE_RIGHT, SITE_LEFT),
                "side-left": (SITE_LEFT, SITE_RIGHT),
            }[edge.kind]
            if abs(start[0] - end[0]) < 0.01 or abs(start[1] - end[1]) < 0.01:
                cxn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, emu(start[0]), emu(start[1]), emu(end[0]), emu(end[1]))
            else:
                cxn = slide.shapes.add_connector(MSO_CONNECTOR.ELBOW, 0, 0, emu(1), emu(1))
                first_leg = {SITE_RIGHT: (1, 0), SITE_LEFT: (-1, 0)}.get(start_site, (0, 1))
                rot, flip_h, flip_v, (fx, fy, fw, fh) = connector_frame(start, end, first_leg)
                sp_pr = cxn._element.spPr
                if edge.kind in ("right", "left"):
                    sp_pr.prstGeom.set("prst", "bentConnector2")  # one bend: down the trunk, then across
                elif edge.kind == "below":  # two bends: put the middle leg where the page draws it
                    bend = (points[1][1] - start[1]) / (end[1] - start[1])
                    av_lst = sp_pr.prstGeom.find(qn("a:avLst"))
                    if av_lst is None:
                        av_lst = sp_pr.prstGeom.makeelement(qn("a:avLst"), {})
                        sp_pr.prstGeom.append(av_lst)
                    av_lst.append(av_lst.makeelement(qn("a:gd"), {"name": "adj1", "fmla": f"val {round(bend * 100000)}"}))
                xfrm = sp_pr.xfrm
                for attr in ("rot", "flipH", "flipV"):
                    xfrm.attrib.pop(attr, None)
                if rot:
                    xfrm.set("rot", str(rot * 60000))
                if flip_h:
                    xfrm.set("flipH", "1")
                if flip_v:
                    xfrm.set("flipV", "1")
                xfrm.off.x, xfrm.off.y = emu(fx), emu(fy)
                xfrm.ext.cx, xfrm.ext.cy = emu(fw), emu(fh)
            glue = cxn._element.nvCxnSpPr.cNvCxnSpPr
            st = glue.get_or_add_stCxn()
            st.id, st.idx = shapes[id(edge.parent)].shape_id, start_site
            en = glue.get_or_add_endCxn()
            en.id, en.idx = shapes[id(edge.child)].shape_id, end_site
            cxn.line.color.rgb = RGBColor(*_hex(edge.colour))
            cxn.line.width = emu(1.5 * scale)
            if edge.dashed:
                cxn.line.dash_style = MSO_LINE_DASH_STYLE.DASH

        # Lines behind boxes: move every connector to the back of the shape tree, after the title.
        tree = slide.shapes._spTree
        for cxn_el in [el for el in tree if el.tag.endswith("}cxnSp")]:
            tree.remove(cxn_el)
            tree.insert(3, cxn_el)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


EXPORTS = {
    "png": ("image/png", render_png),
    "pdf": ("application/pdf", render_pdf),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", render_pptx),
}


def export_note(prefix=""):
    return f"{prefix}As of {timezone.localdate():%B %d, %Y}. Prototype, fake data only."
