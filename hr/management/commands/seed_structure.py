"""Seeds the approved structure of the Office of the Municipal Mayor (DECISIONS O7).

Source: the MO organizational chart (the current structure; colours follow its legend) and
the "Organizational Structure" draft, whose sections are added where they fit the chart.
Positions in the draft but not on the chart are "proposed for creation".

Only fills an office that has no structure yet, so changes made in the admin are kept.
--replace rebuilds it from this file.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from hr.audit import disable_audit
from hr.models import Office, OrgUnit, StructurePosition

E, F, P = StructurePosition.Status.ELECTED, StructurePosition.Status.FILLED, StructurePosition.Status.PROPOSED

MAYORS_OFFICE = "Office of the Municipal Mayor"

# (title, salary grade, status) for a position; ("section", name, [positions]) for a section.
STRUCTURE = {
    "head": ("Municipal Mayor I", 27, E),
    # Beside the Mayor: (title, SG, status, coterminous). Left column first, then right.
    "beside": [
        ("Security Officer II", 15, F, False),
        ("Senior Administrative Assistant III (Private Secretary II)", 15, F, False),
        ("Executive Assistant II", 17, P, True),
    ],
    "bodies": ["Local Special Bodies"],  # beside the Mayor, after the positions
    "divisions": [
        ("Cooperatives Development Division", "CDD", [
            ("Cooperatives Development Specialist III", 18, P),
            ("section", "Cooperatives Organization and Registration Facilitation Section", [
                ("Cooperatives Development Specialist II", 15, F),
                ("Cooperatives Development Specialist I", 11, P),
                ("Administrative Aide III (Driver)", 3, P),
            ]),
            ("section", "Planning and Technical Services Section", [
                ("Cooperatives Development Specialist II", 15, P),
                ("Administrative Aide IV (Clerk II)", 4, P),
            ]),
            ("section", "Research and Policy Development Section", [
                ("Cooperatives Development Specialist II", 15, P),
                ("Administrative Aide IV (Clerk II)", 4, P),
            ]),
        ]),
        ("Ordinance Enforcement, Public Safety and Traffic Management Division", "", [
            ("Traffic Operations Officer II", 15, P),
            ("Traffic Operations Officer I", 11, P),
            ("Administrative Officer I", 10, P),
            ("Assistant Traffic Operation Officer", 8, P),
            ("Traffic Aide I", 3, P),
            ("Traffic Aide I", 3, P),
        ]),
        ("Records Management and Administrative Services Division", "", [
            ("Supervising Administrative Officer (Records Officer IV)", 22, P),
            ("Administrative Officer V (Records Officer III)", 18, F),
            ("Administrative Officer I", 10, P),
            ("Administrative Assistant I (Bookbinder)", 7, F),
        ]),
        ("Community Affairs and Development Division", "", [
            ("section", "Community Affairs Section", [
                ("Community Affairs Officer II", 15, P),
                ("Community Affairs Officer I", 11, P),
                ("Community Affairs Assistant II", 8, P),
            ]),
            ("Administrative Officer I (Sports and Games Regulation Officer I)", 10, F),
        ]),
        ("Civil Security Services Division", "CSSD", [
            ("Security Officer I", 11, P),
            ("Security Guard II", 5, P),
            ("Security Guard I", 3, F),
        ]),
        ("General Services Division", "", [
            ("Administrative Officer III (Supply Officer II)", 14, P),
            ("Public Service Foreman", 6, F),
            ("Driver I", 3, F),
            ("Messenger", 2, F),
        ]),
        ("Public Relations, Information and Computer Technology Division", "", [
            ("section", "Information and Public Relations Section", [
                ("Administrative Officer IV (Public Relations Officer II)", 15, P),
                ("Public Relations Officer", 11, F),
                ("Administrative Assistant II (Public Relations Assistant)", None, P),
            ]),
            ("Public Service Foreman", 6, F),
            ("Administrative Aide III (Driver I)", 3, F),
            ("Computer Programmer I", 11, P),
            ("Computer Maintenance Technologist I", 11, P),
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

    def add(items, unit):
        for item in items:
            if item[0] == "section":
                section = OrgUnit.objects.create(
                    office=office, parent=unit, name=item[1], kind=OrgUnit.Kind.SECTION, order=next(order)
                )
                add(item[2], section)
            else:
                title, sg, status = item
                StructurePosition.objects.create(
                    office=office, unit=unit, title=title, salary_grade=sg, status=status, order=next(order)
                )

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
