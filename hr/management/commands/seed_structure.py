"""Seeds the approved structure of the Office of the Municipal Mayor (DECISIONS O7).

Source: HRMO's MO organizational structure (two slides, divisions 1-3 and 4-6); colours follow
its legend. Each position is drawn under the one it reports to, so a section reads as a column
from its top position down.

Only fills an office that has no structure yet, so changes made in the admin are kept.
--replace rebuilds it from this file.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from hr.audit import disable_audit
from hr.models import Office, OrgUnit, StructurePosition

E, F, P = StructurePosition.Status.ELECTED, StructurePosition.Status.FILLED, StructurePosition.Status.PROPOSED

MAYORS_OFFICE = "Office of the Municipal Mayor"


def pos(title, sg, status, *below, joins=False):
    """A position and the ones drawn under it. joins: under the row of positions above it,
    joined to the bottom of each (a driver shared by two sections)."""
    return {"title": title, "sg": sg, "status": status, "below": list(below), "joins": joins}


def chain(*positions):
    """Positions in a column, each under the one before it."""
    *rest, last = positions
    for upper in reversed(rest):
        upper["below"].append(last)
        last = upper
    return last


def section(name, *positions):
    return ("section", name, list(positions))


def traffic_aides():
    return chain(pos("Traffic Aid I", 3, P), pos("Traffic Aid I", 3, P))


def trade(name):
    return chain(
        pos(f"Administrative Assistant II ({name} Foreman)", 8, P),
        pos(f"Administrative Aide V ({name} II)", 5, P),
        pos(f"Administrative Aide III ({name} I)", 3, P),
        pos("Administrative Aide I (Laborer I)", 1, P),
    )


# Divisions: (name, acronym, [top positions, then sections]). A division's sections are drawn
# under its top position (its chief) when it has exactly one.
STRUCTURE = {
    "head": ("Municipal Mayor I", 27, E),
    # Beside the Mayor, in a row just below the box: (title, SG, status, coterminous). Left half first.
    "beside": [
        ("Security Officer II", 15, F, False),
        ("Senior Administrative Assistant III (Private Secretary II)", 15, F, False),
        ("Executive Assistant II", 17, P, False),
    ],
    "bodies": ["Local Special Bodies"],  # beside the Mayor after the positions, dashed
    "divisions": [
        ("Cooperatives Development Division", "CDD", [
            pos("Cooperatives Development Specialist III", 18, P,
                pos("Administrative Aide III (Driver)", 3, P, joins=True)),
            section("Cooperative Development & Registration Facilitation Section", chain(
                pos("Cooperatives Development Specialist II", 15, F),
                pos("Cooperatives Development Specialist I", 11, P),
            )),
            section("Cooperative Monitoring, Research & Compliance Section", chain(
                pos("Cooperatives Development Specialist II", 15, P),
                pos("Administrative Aide IV (Clerk II)", 4, P),
            )),
        ]),
        ("Community Affairs and Public Relations Division", "CAPRD", [
            pos("Administrative Officer V", 18, P,
                pos("Administrative Aide III (Driver)", 3, P, joins=True)),
            section("Community Affairs & Barangay Coordination Section", chain(
                pos("Community Affairs Officer II", 15, P),
                pos("Community Affairs Officer I", 11, P),
                pos("Community Affairs Assistant II", 8, P),
            )),
            section("Public Relations Section", chain(
                pos("Administrative Officer IV (Public Relations Officer II)", 15, P),
                pos("Administrative Officer II (Public Relations Officer I)", 11, F),
                pos("Administrative Assistant II (Public Relations Assistant)", 8, P),
            )),
        ]),
        ("General Services Division", "GSD", [
            pos("General Services Officer / Administrative Officer V", 18, P),
            section("Records & Archival Management Section", pos(
                "Administrative Officer III (Records Officer II)", 14, P,
                pos("Administrative Officer I (Records Officer I)", 10, P),
                pos("Administrative Officer I (Records Officer I)", 10, P),
                pos("Administrative Assistant I (Bookbinder)", 7, F, joins=True),
            )),
            section("Procurement and Supplies Section", chain(
                pos("Administrative Officer III (Supply Officer II)", 14, P),
                pos("Administrative Officer I (Supply Officer I)", 10, P),
                pos("Administrative Assistant III (Buyer III)", 9, P),
                pos("Warehouseman II", 8, P),
            )),
            section("Asset & Facilities Management, Maintenance, Utility & Security Section", chain(
                pos("Administrative Officer IV", 15, P),
                pos(
                    "Administrative Officer II", 11, P,
                    chain(
                        pos("Security Officer I", 11, P),
                        pos("Security Guard II", 5, P),
                        pos("Security Guard I", 3, F),
                        pos("Security Guard I", 3, P),
                        pos("Security Guard I", 3, P),
                    ),
                    chain(
                        pos("Administrative Aide III (Driver I)", 3, F),
                        pos("Driver I", 3, F),
                        pos("Messenger", 2, F),
                        pos("Administrative Aide I (Utility Worker I)", 1, P),
                        pos("Administrative Aide I (Utility Worker I)", 1, P),
                    ),
                    pos(
                        "Administrative Assistant II (Labor General Foreman)", 8, P,
                        pos("Public Service Foreman", 6, F),
                        pos("Public Service Foreman", 6, F),
                        trade("Mason"),
                        trade("Carpenter"),
                    ),
                ),
            )),
        ]),
        ("Information Management Systems Division", "IMSD", [
            pos("Information Technology Officer I", 19, P),
            section("Systems & Software Development Section", chain(
                pos("Information Systems Analyst II", 16, P),
                pos("Information Systems Analyst I", 12, P),
                pos("Information Systems Researcher I", 10, P),
            )),
            section("IT Infrastructure, Technical Support & Maintenance Section", chain(
                pos("Computer Programmer II", 15, P),
                pos("Computer Programmer I", 11, P),
                pos("Computer Maintenance Technologist I", 11, P),
            )),
            section("Data Management & Cybersecurity Section", chain(
                pos("Computer Programmer II", 15, P),
                pos("Senior Administrative Assistant I (Data Controller IV)", 13, P),
                pos("Computer File Librarian I", 8, P),
            )),
        ]),
        ("Public Safety Division", "PSD", [
            pos("Traffic Operations Officer III", 18, P),
            section("Traffic Management Section", chain(
                pos("Traffic Operations Officer II", 15, P),
                pos("Traffic Operations Officer I", 11, P),
                pos("Assistant Traffic Operations Officer", 8, P, traffic_aides(), traffic_aides(), traffic_aides()),
            )),
            section("Ordinance Enforcement (General) Section", chain(
                *[pos("Enforcer / Public Safety Officer", None, P) for _ in range(5)]
            )),
            section("Command & Control Section", chain(
                pos("Electronics & Communications Equipment Technician IV", 15, P),
                pos("Communications Equipment Operator V", 13, P),
                pos("Communications Equipment Operator III", 9, P),
                pos("Communications Equipment Operator II", 6, P),
            )),
        ]),
        ("Sports Development and Regulation Division", "SDRD", [
            section("Sports Development Section"),
            section("Sports Regulation Section"),
        ]),
    ],
}


def seed(office, data):
    order = iter(range(1000))
    StructurePosition.objects.create(office=office, title=data["head"][0], salary_grade=data["head"][1], status=data["head"][2], order=next(order))
    for title, sg, status, coterminous in data["beside"]:
        StructurePosition.objects.create(
            office=office, title=title, salary_grade=sg, status=status, beside_head=True,
            coterminous=coterminous, order=next(order),
        )
    for name in data["bodies"]:
        OrgUnit.objects.create(office=office, name=name, kind=OrgUnit.Kind.BODY, beside_head=True, order=next(order))

    def add_position(item, unit, upper=None):
        position = StructurePosition.objects.create(
            office=office, unit=unit, title=item["title"], salary_grade=item["sg"], status=item["status"],
            reports_to=upper, joins_row=item["joins"], order=next(order),
        )
        for below in item["below"]:
            add_position(below, unit, position)

    def add(items, unit):
        for item in items:
            if isinstance(item, tuple):
                sub = OrgUnit.objects.create(
                    office=office, parent=unit, name=item[1], kind=OrgUnit.Kind.SECTION, order=next(order)
                )
                add(item[2], sub)
            else:
                add_position(item, unit)

    for name, acronym, items in data["divisions"]:
        division = OrgUnit.objects.create(office=office, name=name, acronym=acronym, order=next(order))
        add(items, division)


class Command(BaseCommand):
    help = "Seeds the approved structure (divisions, sections, positions) of the Office of the Municipal Mayor."

    def add_arguments(self, parser):
        parser.add_argument("--replace", action="store_true", help="Rebuild it even if it already exists.")

    def handle(self, *args, **options):
        office = Office.objects.filter(name=MAYORS_OFFICE).first()
        if office is None:
            self.stdout.write("Office of the Municipal Mayor not found. Run seed_offices first.")
            return
        exists = office.units.exists() or office.structure_positions.exists()
        if exists and not options["replace"]:
            self.stdout.write("Mayor's Office structure already there; kept (use --replace to rebuild).")
            return
        with disable_audit(), transaction.atomic():
            office.structure_positions.all().delete()
            office.units.all().delete()
            seed(office, STRUCTURE)
        self.stdout.write(self.style.SUCCESS(
            f"Seeded Mayor's Office structure: {office.units.count()} units, {office.structure_positions.count()} positions."
        ))
