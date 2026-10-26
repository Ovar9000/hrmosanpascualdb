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
DIV_W, STACK_INDENT = 180, 14  # structure chart: a division column, and each level under it
STACK_MIN = 3  # this many childless children hang in a column instead of a wide row
TWO_SIDED_MIN = 5  # and this many hang on both sides of the trunk
MARGIN = 16
REGULAR, BOLD = "Helvetica", "Helvetica-Bold"

INK, MUTED, LINE = "#212529", "#6c757d", "#adb5bd"
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
# Structure charts follow the legend of the office's own chart (DECISIONS O7): (accent, fill).
STRUCTURE_STYLES = {
    StructurePosition.Status.ELECTED: ("#e0a800", "#fff3bf"),
    StructurePosition.Status.PROPOSED: ("#e03131", "#ffe3e3"),
    StructurePosition.Status.FILLED: ("#2f9e44", "#e6f4ea"),
    OrgUnit.Kind.DIVISION: ("#e8590c", "#ffe8d9"),
    OrgUnit.Kind.SECTION: ("#e8590c", "#ffffff"),
    OrgUnit.Kind.BODY: ("#1c7ed6", "#e7f5ff"),
}
STYLES.update({key: accent for key, (accent, _fill) in STRUCTURE_STYLES.items()})
FILLS = {key: fill for key, (_accent, fill) in STRUCTURE_STYLES.items()}
STRUCTURE_LEGEND = [
    (StructurePosition.Status.ELECTED.label, StructurePosition.Status.ELECTED),
    (StructurePosition.Status.PROPOSED.label, StructurePosition.Status.PROPOSED),
    (StructurePosition.Status.FILLED.label, StructurePosition.Status.FILLED),
    ("Division / section", OrgUnit.Kind.DIVISION),
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
    stack: bool = False  # structure chart: children listed in a column under the box, indented
    dashed: bool = False  # the line to this box is dashed (coterminous)
    children: list = field(default_factory=list)
    side: list = field(default_factory=list)  # coterminous: stacked beside this box, not below it
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
        if self.emphasis:
            return self.accent
        return HIGHLIGHT_FILL if self.badge else FILLS.get(self.style, "#ffffff")

    def ink(self, colour):
        """Text colour on this box: dark on a light filled head box, white and light grey on a dark one."""
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
    # "below" (child in a row underneath), "right" / "left" (child hangs beside the trunk),
    # "side-right" / "side-left" (child beside the parent's box, line from its side),
    # "stack" (structure chart: child listed under the parent, line down its left edge)
    kind: str

    @property
    def dashed(self):
        return self.child.dashed

    @property
    def points(self):
        p, c = self.parent, self.child
        px, pb = p.x + p.w / 2, p.y + p.h
        if self.kind == "below":
            cx, mid = c.x + c.w / 2, c.y - V_GAP / 2  # just above the row, below any side band
            return [(px, pb), (px, mid), (cx, mid), (cx, c.y)]
        cy = c.y + c.h / 2
        if self.kind == "stack":
            tx = p.x + STACK_INDENT / 2
            return [(tx, pb), (tx, cy), (c.x, cy)]
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


def _prepare_text(node):
    inner = node.w - 2 * PAD_X
    specs = []
    if node.badge:
        specs.append((node.badge.upper(), BOLD, 8, HIGHLIGHT_BORDER, 1))
    if node.tag:
        specs.append((node.tag.upper(), BOLD, 7.5, MUTED, 1))
    big = HEAD_TEXT if node.emphasis else (0.9 if node.stack else 1)
    specs += [
        (node.title, BOLD, 11 * big, INK, 3 if node.stack else 2),
        (node.subtitle, REGULAR, 9.5 * big, INK, 2),
        (node.detail, REGULAR, 8.5 * big, MUTED, 1),
        (node.more, BOLD, 8.5, node.accent, 1),
    ]
    pad = PAD_Y * 0.6 if node.stack else PAD_Y
    cursor = ACCENT + pad
    node.lines = []
    for text, font, size, colour, max_lines in specs:
        for line in wrap(text, font, size, inner, max_lines):
            node.lines.append((line, font, size, node.ink(colour), cursor + size))
            cursor += size * LINE_SPACING
    node.h = cursor + pad


# --- Layout -------------------------------------------------------------------------


def _hangs(node):
    return not node.grid and len(node.children) >= STACK_MIN and all(not c.children for c in node.children)


def _grid_rows(node):
    """True when the children go in rows of GRID_SIDE either side of the trunk (office tree)."""
    kids = node.children
    return node.grid and len(kids) > GRID_ROW_MAX and all(not c.children and not c.side for c in kids)


def _split_side(node):
    """Coterminous boxes as (left column, right column); a single one goes on the right."""
    half = len(node.side) // 2
    return node.side[:half], node.side[half:]


def _measure_side(node):
    """How far the box and its coterminous columns reach left and right of the box centre."""
    if not node.side:
        return node.w / 2, node.w / 2
    left, right = _split_side(node)
    reach = node.w / 2 + COTERM_GAP + BOX_W
    return (reach if left else node.w / 2), (reach if right else node.w / 2)


def _measure(node):
    """Sets node._width (whole subtree) and node._anchor (box centre, from the subtree's left)."""
    if node.stack:  # the children sit inside the box's own width
        for child in node.children:
            _measure(child)
        node._width, node._anchor = node.w, node.w / 2
        return
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


def _place_side(node, edges):
    """Stacks the coterminous boxes beside the box, the first row level with it. Done here, not
    in _measure, because a row of boxes only gets its shared height after it is measured.
    Returns how far they run below the box, so the children can start under them."""
    if not node.side:
        return 0
    left, right = _split_side(node)
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
    return max([node.y + node.h] + [_bottom(c) for c in node.children])


def _place(node, left, top, edges):
    node.x, node.y = left + node._anchor - node.w / 2, top
    if node.stack:
        y = node.y + node.h + ROW_GAP / 1.5
        for child in node.children:
            _place(child, node.x + STACK_INDENT, y, edges)
            edges.append(Edge(node, child, "stack"))
            y = _bottom(child) + ROW_GAP / 1.5
        return
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
    if not _hangs(node):
        for child, offset in zip(node.children, node._offsets):
            _place(child, left + offset, top + node.h + V_GAP, edges)
            edges.append(Edge(node, child, "below"))
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
        for child in node.side + node.children:
            if node.stack:
                child.stack, child.w = True, node.w - STACK_INDENT
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


def _position_node(position, compact=False):
    """compact: the grade on the title line, for the columns under a division."""
    grade = f"SG {position.salary_grade}" if position.salary_grade else ""
    return ChartNode(
        title=f"{position.title} · {grade.replace(' ', chr(0xa0))}" if compact and grade else position.title,
        subtitle="" if compact else grade,
        style=position.status,
        dashed=position.coterminous,
    )


def _unit_node(unit, units, positions):
    """A division or section box with what is under it, in the order HRMO set."""
    node = ChartNode(
        title=f"{unit.name} ({unit.acronym})" if unit.acronym else unit.name,
        style=unit.kind,
        dashed=unit.beside_head,
    )
    items = [(u.order, _unit_node(u, units, positions)) for u in units if u.parent_id == unit.pk]
    items += [(p.order, _position_node(p, compact=True)) for p in positions if p.unit_id == unit.pk]
    node.children = [n for _order, n in sorted(items, key=lambda item: item[0])]
    return node


def build_structure(office, base):
    """The office's own structure chart (DECISIONS O7): its head position at the top, the
    positions and bodies beside the head, and each division as a column of its sections and
    positions. base: the office's box on the office tree (its detail line and link)."""
    units = list(office.units.all())
    positions = list(office.structure_positions.all())
    head_position = next((p for p in positions if p.unit_id is None and not p.beside_head), None)
    head = ChartNode(
        title=head_position.title if head_position else office.name,
        subtitle=f"SG {head_position.salary_grade}" if head_position and head_position.salary_grade else office.head_title,
        detail=base.detail,
        style=head_position.status if head_position else office.branch,
        url=base.url,
    )
    side = [(p.order, _position_node(p)) for p in positions if p.unit_id is None and p.beside_head]
    side += [(u.order, _unit_node(u, units, positions)) for u in units if u.parent_id is None and u.beside_head]
    head.side = [n for _order, n in sorted(side, key=lambda item: item[0])]
    for unit in sorted((u for u in units if u.parent_id is None and not u.beside_head), key=lambda u: u.order):
        division = _unit_node(unit, units, positions)
        division.stack, division.w = True, DIV_W
        head.children.append(division)
    return head


def build_office_tree(detail_office_ids=(), linked_office_ids=(), focus=None, whole=False):
    """The municipal office tree. Head names and staff counts only for detail_office_ids.

    focus: pk of an office to draw as the head at the top, with its divisions under it and its
    coterminous offices beside it (DECISIONS O6). Without it, the municipality is the head with
    the top offices under it. Offices one level further down are folded: their box says what
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
    legend = []
    if focus in nodes:
        head = nodes[focus]
        if focus in has_structure:  # its own chart, with its sub-offices still beside or under it
            office_node = head
            head = build_structure(by_pk[focus], office_node)
            head.side += office_node.side
            head.children += office_node.children
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
    head.emphasis, head.w = True, HEAD_W

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
        out.append(f'<polyline points="{points}" fill="none" stroke="{LINE}" stroke-width="1.5"{dash}/>')
    for node in chart.nodes:
        if node.url:
            out.append(f'<a href="{escape(node.url)}">')
        out.append("<g>")
        out.append(f"<title>{escape(' · '.join(t for t in (node.title, node.subtitle, node.detail) if t))}</title>")
        width = 2.5 if node.badge else 1.5
        out.append(
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.w:.1f}" height="{node.h:.1f}" rx="5" '
            f'fill="{node.fill}" stroke="{node.accent}" stroke-width="{width}"/>'
        )
        if not node.emphasis:
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


def render_pdf(chart, heading, note=""):
    """One-page vector PDF: A4 landscape, then A3, at no less than 60% size; otherwise a page
    as large as the chart (it still prints with "fit to page")."""
    from reportlab.lib.pagesizes import A3, A4, landscape
    from reportlab.pdfgen import canvas

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

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=page)
    pdf.setTitle(heading)
    pdf.setAuthor("San Pascual HR")
    pdf.setFont(BOLD, 14)
    pdf.drawString(margin, page_h - margin - 12, heading)
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

    pdf.setStrokeColorRGB(*[c / 255 for c in _hex(LINE)])
    pdf.setLineWidth(1.5 * scale)
    for edge in chart.edges:
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
        pdf.roundRect(x, y_top - h, w, h, 5 * scale, stroke=1, fill=1)
        if not node.emphasis:
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

    line_w = max(1, int(1.5 * scale))
    for edge in chart.edges:
        points = [px(x, y) for x, y in edge.points]
        if not edge.dashed:
            draw.line(points, fill=LINE, width=line_w, joint="curve")
            continue
        on, off = DASH[0] * scale, DASH[1] * scale  # PIL has no dashes: draw them one by one
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            length = math.hypot(x1 - x0, y1 - y0)
            start = 0
            while start < length:
                end = min(start + on, length)
                a, b = start / length, end / length
                draw.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=LINE, width=line_w)
                start += on + off
    for node in chart.nodes:
        x0, y0 = px(node.x, node.y)
        x1, y1 = px(node.x + node.w, node.y + node.h)
        border = int((2.5 if node.badge else 1.5) * scale)
        draw.rounded_rectangle([x0, y0, x1, y1], radius=5 * scale, fill=node.fill, outline=node.accent, width=border)
        if not node.emphasis:
            draw.rectangle([x0, y0, x1, y0 + ACCENT * scale], fill=node.accent)
        max_w = (node.w - 2 * PAD_X) * scale
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
    """One editable slide: every box is a rounded rectangle and every line a connector glued to
    its two boxes, so moving a box in PowerPoint drags its lines along."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_LINE_DASH_STYLE
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Emu, Pt

    margin, head = 24, HEADING_SPACE + 12  # room for a slide-sized title
    slide_w, slide_h, max_side = 960, 540, 4032  # 16:9 in points; PowerPoint allows up to 56 in
    scale = min(1, (slide_w - 2 * margin) / chart.width, (slide_h - head - margin) / chart.height)
    if scale < 0.5:  # too small to read on 16:9: grow the slide instead
        scale = min(0.5, (max_side - 2 * margin) / chart.width, (max_side - head - margin) / chart.height)
        slide_w = max(slide_w, chart.width * scale + 2 * margin)
        slide_h = max(slide_h, chart.height * scale + head + margin)
    left = (slide_w - chart.width * scale) / 2

    def emu(points):
        return Emu(int(round(points * 12700)))

    def to_slide(x, y):
        return left + x * scale, head + y * scale

    prs = Presentation()
    prs.slide_width, prs.slide_height = emu(slide_w), emu(slide_h)
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    legend_w = sum(stringWidth(label, REGULAR, LEGEND_SIZE) + LEGEND_KEY + 18 for label, _style in chart.legend)
    title = slide.shapes.add_textbox(emu(margin), emu(8), emu(slide_w - 2 * margin - legend_w), emu(head - 8))
    title.text_frame.word_wrap = True
    for i, (text, size, bold, colour) in enumerate(((heading, 20, True, INK), (note, 10, False, MUTED))):
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
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, emu(x), emu(y), emu(node.w * scale), emu(node.h * scale))
        shape.name = node.title[:60]
        shape.adjustments[0] = 0.08
        shape.shadow.inherit = False
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(*_hex(node.fill))
        shape.line.color.rgb = RGBColor(*_hex(node.accent))
        shape.line.width = emu((2.5 if node.badge else 1.5) * scale)
        frame = shape.text_frame
        frame.word_wrap = True
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        frame.margin_left = frame.margin_right = emu(PAD_X * scale / 2)
        frame.margin_top = frame.margin_bottom = emu(PAD_Y * scale / 2)
        # Whole strings, not the wrapped lines: PowerPoint wraps them again as people edit.
        parts = [(node.badge.upper(), 8, True, HIGHLIGHT_BORDER)] if node.badge else []
        parts += [(node.tag.upper(), 7.5, True, MUTED)] if node.tag else []
        big = HEAD_TEXT if node.emphasis else 1
        parts += [(node.title, 11 * big, True, INK), (node.subtitle, 9.5 * big, False, INK), (node.detail, 8.5 * big, False, MUTED)]
        parts.append((node.more, 8.5, True, node.accent))
        parts = [(text, size, bold, node.ink(colour)) for text, size, bold, colour in parts]
        first = True
        for text, size, bold, colour in parts:
            if not text:
                continue
            para = frame.paragraphs[0] if first else frame.add_paragraph()
            first = False
            para.alignment = PP_ALIGN.CENTER
            run = para.add_run()
            run.text = text
            run.font.size, run.font.bold, run.font.name = Pt(size * scale), bold, "Arial"
            run.font.color.rgb = RGBColor(*_hex(colour))
        shapes[id(node)] = shape

    for edge in chart.edges:
        points = [to_slide(x, y) for x, y in edge.points]
        start, end = points[0], points[-1]
        start_site, end_site = {
            "stack": (SITE_BOTTOM, SITE_LEFT),
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
            if edge.kind in ("right", "left", "stack"):
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
        cxn.line.color.rgb = RGBColor(*_hex(LINE))
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
