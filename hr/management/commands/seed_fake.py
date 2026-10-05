"""Seeds fake HR data for the Municipality of San Pascual scaled to ~420 personnel.

Strictly fake data only (DECISIONS D1).
Scales the fake dataset to match the real municipal workforce composition (Survey Oct 2026):
- 12 Elected (Mayor, Vice Mayor, 8 SB Members, ABC President, SK Federation President)
- 94 Permanent Plantilla (22 Office Heads + 12 Career-progressed + 60 permanent staff)
- 28 Casual
- 286 JO / COS / Project-based (~68% of municipal workforce)
Total = 420 personnel across all 25 offices.
Includes career progression history (JO -> Casual -> Plantilla), leave credit ledgers,
physical 201-file document tracking, and time-bound executive delegation.
"""

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from hr.audit import disable_audit
from hr.models import (
    Delegate,
    Document,
    Engagement,
    LeaveCredit,
    LeaveRequest,
    LeaveType,
    Office,
    Person,
    Project,
)

User = get_user_model()

FILIPINO_LAST_NAMES = [
    "Dela Cruz", "Santos", "Reyes", "Garcia", "Mendoza", "Torres", "Bautista",
    "Flores", "Villanueva", "Ramos", "Castro", "Rivera", "Aquino", "Morales",
    "Mercado", "Tolentino", "Tan", "Salazar", "Delos Reyes", "Soriano",
    "Perez", "Navarro", "Gomez", "Hernandez", "Lim", "Espiritu", "Magbanua",
    "Dimaculangan", "Custodio", "Alcantara", "Valenzuela", "Corpus", "Miranda",
    "Macaraeg", "Pascual", "Cabrera", "Manalo", "Ilagan", "Marasigan", "Atienza",
    "Katigbak", "Malabanan", "Evangelista", "Vergara", "Castillo", "De Castro",
    "Bustamante", "Cervantes", "Salcedo", "Gutierrez", "Santiago", "Bernardo",
    "Agoncillo", "Ricarte", "Malvar", "Laurel", "Mabini", "Arellano", "Guerrero",
    "Alvarado", "Ocampo", "Concepcion", "Crisostomo", "Panganiban", "San Jose",
    "De Leon", "Pangilinan", "Dimayuga", "Macatangay", "Buendia", "Magallanes",
]

FILIPINO_MALE_FIRST_NAMES = [
    "Juan", "Jose", "Pedro", "Mark Anthony", "John Paul", "Ramon", "Rodrigo",
    "Danilo", "Eduardo", "Reynaldo", "Noel", "Ferdinand", "Angelo", "Christian",
    "Jerome", "Rolando", "Renato", "Arnold", "Arnel", "Efren", "Rommel",
    "Vicente", "Gabriel", "Rafael", "Emmanuel", "Lito", "Nestor", "Cesar",
    "Manuel", "Antonio", "Jaime", "Rogelio", "Ernesto", "Arturo", "Alfredo",
    "Benjamin", "Domingo", "Salvador", "Ruben", "Leonardo", "Gregorio", "Mario",
    "Wilfredo", "Rene", "Alexander", "Felipe", "Teodoro", "Enrique", "Rodolfo",
]

FILIPINO_FEMALE_FIRST_NAMES = [
    "Maria", "Christine", "Mary Grace", "Rowena", "Maricel", "Angelica",
    "Carmela", "Lourdes", "Corazon", "Imelda", "Divina", "Rosario", "Teresa",
    "Marites", "Jocelyn", "Bernadette", "Clarissa", "Aileen", "Arlene", "Leah",
    "Rhea", "Camille", "Patricia", "Kristine", "Janice", "Melody", "Sheryl",
    "Leonor", "Esperanza", "Milagros", "Consuelo", "Carmencita", "Norma",
    "Leticia", "Aurora", "Flordeliza", "Vilma", "Nenita", "Rowena", "Glenda",
    "Gwendolyn", "Racquel", "Charmaine", "Liezl", "Lorena", "Hazel", "Abigail",
]

PROJECTS_DATA = [
    ("San Pascual Central Public Market Modernization", "Office of the Municipal Engineer"),
    ("Municipal Multi-Purpose Evacuation Center Phase 2", "Municipal Disaster Risk Reduction and Management Office (MDRRMO)"),
    ("Coastal Road Concreting and Shoreline Protection", "Office of the Municipal Engineer"),
    ("Mangrove Reforestation and Eco-Tourism Trail", "Office of the Municipal Environment and Natural Resources Officer"),
    ("San Pascual Digital Registry & GIS Mapping", "Office of the Municipal Planning and Development Coordinator"),
    ("Rural Health Unit Birthing Facility Upgrade", "Office of the Municipal Health Officer"),
]

SALARY_SCHEDULE_STEP_1 = {
    1: Decimal("13530.00"), 2: Decimal("14290.00"), 3: Decimal("15080.00"),
    4: Decimal("15910.00"), 5: Decimal("16790.00"), 6: Decimal("17720.00"),
    7: Decimal("18700.00"), 8: Decimal("19740.00"), 9: Decimal("21211.00"),
    10: Decimal("23176.00"), 11: Decimal("27000.00"), 12: Decimal("29165.00"),
    13: Decimal("31320.00"), 14: Decimal("33843.00"), 15: Decimal("36619.00"),
    16: Decimal("39672.00"), 17: Decimal("43030.00"), 18: Decimal("46725.00"),
    19: Decimal("51357.00"), 20: Decimal("56627.00"), 21: Decimal("63997.00"),
    22: Decimal("71578.00"), 23: Decimal("80003.00"), 24: Decimal("90078.00"),
    25: Decimal("102690.00"), 26: Decimal("116040.00"),
}


class Command(BaseCommand):
    help = "Seeds fake people scaled to ~420 municipal workforce (Survey Oct 2026)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing people and engagements before seeding.",
        )

    def handle(self, *args, **options):
        with disable_audit():
            # Ensure offices are seeded
            if Office.objects.count() < 25:
                self.stdout.write("Offices not fully seeded. Running seed_offices first...")
                call_command("seed_offices")

            if options["clear"]:
                self.stdout.write("Clearing existing HR records...")
                with transaction.atomic():
                    Delegate.objects.all().delete()
                    LeaveCredit.objects.all().delete()
                    LeaveRequest.objects.all().delete()
                    Document.objects.all().delete()
                    Engagement.objects.all().delete()
                    Project.objects.all().delete()
                    Person.objects.all().delete()
                self.stdout.write(self.style.WARNING("Existing HR records deleted."))

            random.seed(42)  # Deterministic seed for reproducible testing

            self.stdout.write("Seeding ~420 fake municipal personnel...")
            with transaction.atomic():
                self._seed_data()

            from hr.sequences import sync_employee_sequence
            seq_val = sync_employee_sequence()
            self.stdout.write(f"Synchronized EmployeeSequence to {seq_val}.")

            self.stdout.write(
                self.style.SUCCESS(
                    f"Done! Database now has {Person.objects.count()} people, "
                    f"{Engagement.objects.count()} engagements across {Office.objects.count()} offices, "
                    f"{Project.objects.count()} projects, {Document.objects.count()} documents, "
                    f"and {LeaveRequest.objects.count()} leave requests."
                )
            )


    def _seed_data(self):
        # 1. Projects
        projects = []
        for name, office_name in PROJECTS_DATA:
            office = Office.objects.get(name=office_name)
            p, _ = Project.objects.get_or_create(
                name=name,
                office=office,
                defaults={
                    "start_date": date(2025, 1, 15),
                    "end_date": date(2026, 12, 31),
                    "status": Project.Status.ONGOING,
                    "notes": "LGU Priority Infrastructure / Environmental Project.",
                },
            )
            projects.append(p)

        offices = list(Office.objects.all())
        used_names = set()
        person_counter = Person.objects.count()

        def make_fake_person(preferred_sex=None, birth_year_range=(1965, 2002)):
            nonlocal person_counter
            sex = preferred_sex or random.choice([Person.Sex.MALE, Person.Sex.FEMALE])
            first_pool = FILIPINO_MALE_FIRST_NAMES if sex == Person.Sex.MALE else FILIPINO_FEMALE_FIRST_NAMES

            while True:
                last_name = random.choice(FILIPINO_LAST_NAMES)
                first_name = random.choice(first_pool)
                middle_name = random.choice(FILIPINO_LAST_NAMES)
                birth_year = random.randint(*birth_year_range)
                birth_month = random.randint(1, 12)
                birth_day = random.randint(1, 28)
                bdate = date(birth_year, birth_month, birth_day)
                key = (last_name.lower(), first_name.lower(), bdate)
                if key not in used_names:
                    used_names.add(key)
                    break

            person_counter += 1
            emp_no = f"SP-{person_counter:05d}"
            # Fake IDs always start with 00 (DECISIONS D1)
            tin = f"00{random.randint(10, 99)}-{random.randint(100, 999)}-{random.randint(100, 999)}-000"
            gsis = f"00{random.randint(100000000, 999999999)}"
            philhealth = f"00-{random.randint(100000000, 999999999)}-0"
            pagibig = f"00{random.randint(10, 99)}-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"
            sss = f"00-{random.randint(1000000, 9999999)}-0"

            p = Person.objects.create(
                employee_no=emp_no,
                last_name=last_name,
                first_name=first_name,
                middle_name=middle_name,
                sex=sex,
                birthdate=bdate,
                mobile=f"09{random.randint(10, 99)}{random.randint(1000000, 9999999)}",
                email=f"{first_name.split()[0].lower()}.{last_name.lower().replace(' ', '')}@sanpascual.gov.ph",
                address=f"Brgy. {random.choice(['Poblacion', 'Ilalim', 'Ibabao', 'San Mateo', 'Gelerang Kawayan', 'Resplandor', 'Kaingin', 'Bayanan', 'Palsahingin', 'Laurel'])}, San Pascual",
                tin=tin,
                gsis_no=gsis,
                philhealth_no=philhealth,
                pagibig_no=pagibig,
                sss_no=sss,
            )
            return p

        # ---------------------------------------------------------------------
        # 2. Elected Officials (Exactly 12 people)
        # ---------------------------------------------------------------------
        # Mayor
        mayor_office = Office.objects.get(name="Office of the Municipal Mayor")
        mayor_person = make_fake_person(preferred_sex=Person.Sex.FEMALE, birth_year_range=(1965, 1978))
        Engagement.objects.create(
            person=mayor_person,
            office=mayor_office,
            type=Engagement.Type.ELECTED,
            position_title="Municipal Mayor",
            rate=Decimal("150000.00"),
            rate_basis=Engagement.RateBasis.MONTHLY,
            start_date=date(2025, 6, 30),
            end_date=date(2028, 6, 30),
            status=Engagement.Status.ACTIVE,
            leave_eligible=False,
        )

        # Vice Mayor
        vm_office = Office.objects.get(name="Office of the Vice Mayor")
        vm_person = make_fake_person(preferred_sex=Person.Sex.MALE, birth_year_range=(1965, 1980))
        Engagement.objects.create(
            person=vm_person,
            office=vm_office,
            type=Engagement.Type.ELECTED,
            position_title="Municipal Vice Mayor",
            rate=Decimal("120000.00"),
            rate_basis=Engagement.RateBasis.MONTHLY,
            start_date=date(2025, 6, 30),
            end_date=date(2028, 6, 30),
            status=Engagement.Status.ACTIVE,
            leave_eligible=False,
        )

        # 8 Sangguniang Bayan Members + 2 Ex-Officio (ABC & SK)
        sb_office = Office.objects.get(name="Office of the Sangguniang Bayan")
        for i in range(1, 9):
            councilor = make_fake_person(birth_year_range=(1968, 1988))
            Engagement.objects.create(
                person=councilor,
                office=sb_office,
                type=Engagement.Type.ELECTED,
                position_title=f"Sangguniang Bayan Member ({i})",
                rate=Decimal("95000.00"),
                rate_basis=Engagement.RateBasis.MONTHLY,
                start_date=date(2025, 6, 30),
                end_date=date(2028, 6, 30),
                status=Engagement.Status.ACTIVE,
                leave_eligible=False,
            )

        # ABC President (Liga ng mga Barangay President, Ex-Officio SB Member)
        abc_person = make_fake_person(birth_year_range=(1965, 1982))
        Engagement.objects.create(
            person=abc_person,
            office=sb_office,
            type=Engagement.Type.ELECTED,
            position_title="LnB President (Ex-Officio SB Member)",
            rate=Decimal("95000.00"),
            rate_basis=Engagement.RateBasis.MONTHLY,
            start_date=date(2023, 11, 30),
            end_date=date(2026, 11, 30),
            status=Engagement.Status.ACTIVE,
            leave_eligible=False,
        )

        # SK Federation President (Ex-Officio SB Member)
        sk_person = make_fake_person(birth_year_range=(1998, 2003))
        Engagement.objects.create(
            person=sk_person,
            office=sb_office,
            type=Engagement.Type.ELECTED,
            position_title="SK Federation President (Ex-Officio SB Member)",
            rate=Decimal("95000.00"),
            rate_basis=Engagement.RateBasis.MONTHLY,
            start_date=date(2023, 11, 30),
            end_date=date(2026, 11, 30),
            status=Engagement.Status.ACTIVE,
            leave_eligible=False,
        )

        # ---------------------------------------------------------------------
        # 3. Department Heads (Plantilla, 22 positions)
        # ---------------------------------------------------------------------
        head_titles = {
            "Office of the Secretary to the Sangguniang Bayan": ("Secretary to the Sangguniang Bayan", 24),
            "Office of the Municipal Administrator": ("Municipal Administrator", 26),
            "Office of the Municipal Human Resources and Management Officer (HRMO)": ("Municipal HRMO", 24),
            "Municipal Civil Registrar": ("Municipal Civil Registrar", 24),
            "Office of the Municipal Treasurer": ("Municipal Treasurer", 24),
            "Business Permits and Licensing Office": ("BPLO Head", 22),
            "Office of the Municipal Budget Officer": ("Municipal Budget Officer", 24),
            "Office of the Municipal Accountant": ("Municipal Accountant", 24),
            "Office of the Municipal Assessor": ("Municipal Assessor", 24),
            "Office of the Municipal Planning and Development Coordinator": ("MPDC Coordinator", 24),
            "Office of the Municipal Engineer": ("Municipal Engineer", 24),
            "Office of the Municipal Agriculturist": ("Municipal Agriculturist", 24),
            "Office of the Municipal Environment and Natural Resources Officer": ("MENRO Head", 24),
            "Municipal Tourism Office": ("Tourism Operations Officer IV", 22),
            "Municipal Disaster Risk Reduction and Management Office (MDRRMO)": ("MDRRM Officer", 24),
            "Municipal Social Welfare and Development Office (MSWDO)": ("MSWDO Head", 24),
            "Office of the Municipal Health Officer": ("Municipal Health Officer", 25),
            "Office of the Municipal Persons with Disability Affairs Officer": ("PDAO Head", 18),
            "Office of the Municipal Public Employment Service Manager": ("PESO Manager", 18),
            "Office of the Municipal Youth Development Officer": ("Youth Development Officer III", 18),
            "Office of the Municipal Population Officer": ("Population Officer III", 18),
            "San Pascual Polytechnic College": ("College Administrator", 25),
        }

        for off_name, (title, sg) in head_titles.items():
            office = Office.objects.get(name=off_name)
            head_person = make_fake_person(birth_year_range=(1968, 1985))
            step = random.randint(1, 4)
            rate = SALARY_SCHEDULE_STEP_1.get(sg, Decimal("70000.00")) + (step - 1) * Decimal("1200.00")
            item_no = f"SP-{office.acronym or 'OFF'}-HD-01"
            Engagement.objects.create(
                person=head_person,
                office=office,
                type=Engagement.Type.PLANTILLA,
                position_title=title,
                rate=rate,
                rate_basis=Engagement.RateBasis.MONTHLY,
                salary_grade=sg,
                step=step,
                item_number=item_no,
                start_date=date(2019, 7, 1),
                status=Engagement.Status.ACTIVE,
                leave_eligible=True,
            )

        # ---------------------------------------------------------------------
        # 4. Career History Progression (12 people: JO -> Casual -> Plantilla)
        # ---------------------------------------------------------------------
        career_offices = [
            "Office of the Municipal Human Resources and Management Officer (HRMO)",
            "Office of the Municipal Treasurer",
            "Office of the Municipal Accountant",
            "Office of the Municipal Engineer",
            "Office of the Municipal Health Officer",
            "Municipal Civil Registrar",
        ]
        for c_idx in range(12):
            person = make_fake_person(birth_year_range=(1985, 1996))
            off = Office.objects.get(name=career_offices[c_idx % len(career_offices)])

            # 1st: Started as Job Order in 2021
            Engagement.objects.create(
                person=person,
                office=off,
                type=Engagement.Type.JOB_ORDER,
                position_title="Job Order Support Staff",
                rate=Decimal("480.00"),
                rate_basis=Engagement.RateBasis.DAILY,
                start_date=date(2021, 2, 1),
                end_date=date(2022, 6, 30),
                status=Engagement.Status.ENDED,
                leave_eligible=False,
                notes="Completed initial JO contract period.",
            )

            # 2nd: Promoted to Casual in 2022
            Engagement.objects.create(
                person=person,
                office=off,
                type=Engagement.Type.CASUAL,
                position_title="Administrative Aide IV (Casual)",
                rate=Decimal("15500.00"),
                rate_basis=Engagement.RateBasis.MONTHLY,
                start_date=date(2022, 7, 1),
                end_date=date(2023, 12, 31),
                status=Engagement.Status.ENDED,
                leave_eligible=True,
                notes="Recommended for regular plantilla appointment.",
            )

            # 3rd: Appointed to permanent Plantilla in 2024 (Active)
            sg = 8 + (c_idx % 4)
            item_no = f"SP-{off.acronym}-REG-{c_idx+1:02d}"
            Engagement.objects.create(
                person=person,
                office=off,
                type=Engagement.Type.PLANTILLA,
                position_title="Administrative Assistant II",
                rate=SALARY_SCHEDULE_STEP_1.get(sg, Decimal("21000.00")),
                rate_basis=Engagement.RateBasis.MONTHLY,
                salary_grade=sg,
                step=1,
                item_number=item_no,
                start_date=date(2024, 1, 1),
                status=Engagement.Status.ACTIVE,
                leave_eligible=True,
                notes="Regular permanent plantilla appointment.",
            )

        # ---------------------------------------------------------------------
        # 5. Additional Plantilla Staff (60 people, total Plantilla = 22 + 12 + 60 = 94)
        # ---------------------------------------------------------------------
        plantilla_positions = [
            ("Administrative Officer V", 18),
            ("Administrative Officer II", 11),
            ("Administrative Assistant I", 7),
            ("Administrative Aide VI", 6),
            ("Revenue Collection Clerk II", 7),
            ("Planning Officer II", 15),
            ("Engineer II", 16),
            ("Engineer I", 12),
            ("Medical Technologist I", 11),
            ("Nurse I", 15),
            ("Midwife II", 8),
            ("Agricultural Technologist", 10),
            ("Social Welfare Officer I", 11),
            ("Environmental Management Specialist I", 11),
            ("Sanitation Inspector I", 6),
            ("Draftsman II", 8),
        ]
        for p_idx in range(60):
            person = make_fake_person(birth_year_range=(1978, 1999))
            off = random.choice(offices)
            title, sg = random.choice(plantilla_positions)
            step = random.choice([1, 2, 3, 4])
            rate = SALARY_SCHEDULE_STEP_1.get(sg, Decimal("22000.00")) + (step - 1) * Decimal("750.00")
            item_no = f"SP-{off.acronym or 'OFF'}-P{p_idx+10:03d}"
            Engagement.objects.create(
                person=person,
                office=off,
                type=Engagement.Type.PLANTILLA,
                position_title=title,
                rate=rate,
                rate_basis=Engagement.RateBasis.MONTHLY,
                salary_grade=sg,
                step=step,
                item_number=item_no,
                start_date=date(2020 + (p_idx % 5), random.randint(1, 12), 1),
                status=Engagement.Status.ACTIVE,
                leave_eligible=True,
            )

        # ---------------------------------------------------------------------
        # 6. Casual Staff (Exactly 28 people)
        # ---------------------------------------------------------------------
        casual_titles = [
            "Utility Worker I (Casual)",
            "Driver II (Casual)",
            "Administrative Aide I (Casual)",
            "Security Guard I (Casual)",
            "Maintenance Worker (Casual)",
        ]
        for c_idx in range(28):
            person = make_fake_person(birth_year_range=(1982, 2001))
            off = random.choice(offices)
            Engagement.objects.create(
                person=person,
                office=off,
                type=Engagement.Type.CASUAL,
                position_title=random.choice(casual_titles),
                rate=Decimal(random.choice(["14000.00", "15200.00", "16500.00"])),
                rate_basis=Engagement.RateBasis.MONTHLY,
                start_date=date(2024, 1, 1),
                end_date=date(2026, 12, 31),
                status=Engagement.Status.ACTIVE,
                leave_eligible=True,
            )

        # ---------------------------------------------------------------------
        # 7. Job Order Staff (242 people, daily rates)
        # ---------------------------------------------------------------------
        jo_titles = [
            "Traffic Aide (JO)",
            "Street Sweeper / Environmental Aide (JO)",
            "Office Encoder (JO)",
            "Disaster Response Responder (JO)",
            "Health Center Assistant (JO)",
            "Market Aide (JO)",
            "Heavy Equipment Assistant (JO)",
            "Day Care Worker Assistant (JO)",
            "Parks and Plaza Maintenance Aide (JO)",
        ]
        for j_idx in range(242):
            person = make_fake_person(birth_year_range=(1985, 2004))
            off = random.choice(offices)
            # Stagger start dates to test tenure calculation in reports (<1 yr, 1-3 yrs, 3-5 yrs)
            years_ago = (j_idx % 6)
            s_date = date(2026 - years_ago, random.randint(1, 12), random.randint(1, 28))
            if s_date > date(2026, 10, 1):
                s_date = date(2026, 1, 15)
            Engagement.objects.create(
                person=person,
                office=off,
                type=Engagement.Type.JOB_ORDER,
                position_title=random.choice(jo_titles),
                rate=Decimal(random.choice(["450.00", "480.00", "500.00", "530.00", "560.00"])),
                rate_basis=Engagement.RateBasis.DAILY,
                start_date=s_date,
                end_date=date(2026, 12, 31),
                status=Engagement.Status.ACTIVE,
                leave_eligible=False,
            )

        # ---------------------------------------------------------------------
        # 8. Contract of Service (COS) (30 people)
        # ---------------------------------------------------------------------
        cos_titles = [
            "IT System Developer / Database Consultant",
            "Legal Consultant",
            "Civil Engineer Consultant",
            "Environmental Management Specialist",
            "Disaster Risk Planning Specialist",
            "Local Economic Development Specialist",
            "Health Policy Consultant",
        ]
        for cs_idx in range(30):
            person = make_fake_person(birth_year_range=(1980, 1997))
            off = random.choice(offices)
            # A couple of COS contracts have negotiated leave eligibility (survey finding)
            is_leave_ok = (cs_idx < 2)
            Engagement.objects.create(
                person=person,
                office=off,
                type=Engagement.Type.CONTRACT_OF_SERVICE,
                position_title=random.choice(cos_titles),
                rate=Decimal(random.choice(["32000.00", "38000.00", "45000.00", "52000.00"])),
                rate_basis=Engagement.RateBasis.MONTHLY,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
                status=Engagement.Status.ACTIVE,
                leave_eligible=is_leave_ok,
            )

        # ---------------------------------------------------------------------
        # 9. Project-Based Staff (14 people linked to Projects)
        # ---------------------------------------------------------------------
        pb_titles = [
            "Project Field Engineer",
            "Construction Safety Officer",
            "GIS Field Surveyor",
            "Project Community Facilitator",
            "Project Materials Inspector",
        ]
        for pb_idx in range(14):
            person = make_fake_person(birth_year_range=(1985, 1998))
            project = projects[pb_idx % len(projects)]
            Engagement.objects.create(
                person=person,
                office=project.office,
                type=Engagement.Type.PROJECT_BASED,
                position_title=random.choice(pb_titles),
                rate=Decimal(random.choice(["28000.00", "34000.00", "42000.00"])),
                rate_basis=Engagement.RateBasis.MONTHLY,
                project=project,
                start_date=project.start_date,
                end_date=project.end_date,
                status=Engagement.Status.ACTIVE,
                leave_eligible=False,
            )

        # ---------------------------------------------------------------------
        # 10. Documents Tracking (Wet-signed original on file, optional scan)
        # ---------------------------------------------------------------------
        people_sample = list(Person.objects.all()[:60])
        cabinets = ["Cabinet 1", "Cabinet 2", "Cabinet 3", "Cabinet 4", "Cabinet 5"]
        drawers = ["Drawer 1", "Drawer 2", "Drawer 3", "Drawer 4"]
        for p in people_sample:
            Document.objects.create(
                person=p,
                doc_type=Document.Type.PDS,
                status=Document.Status.FILED,
                original_on_file=True,
                physical_location=f"{random.choice(cabinets)}, {random.choice(drawers)}, File-{p.employee_no}",
                notes="Personal Data Sheet (CS Form No. 212, Revised 2017)",
            )
            eng = p.engagements.first()
            if eng:
                Document.objects.create(
                    engagement=eng,
                    doc_type=Document.Type.APPOINTMENT,
                    status=Document.Status.APPROVED,
                    original_on_file=True,
                    physical_location=f"{random.choice(cabinets)}, {random.choice(drawers)}, File-{p.employee_no}",
                    notes="Approved appointment paper / wet-signed contract.",
                )

        # ---------------------------------------------------------------------
        # 11. Leave Credits & Leave Requests
        # ---------------------------------------------------------------------
        eligible_people = [p for p in Person.objects.all() if p.earns_leave_credits()]
        for p in eligible_people[:80]:
            vl_days = Decimal(str(random.randint(12, 30)))
            sl_days = Decimal(str(random.randint(10, 25)))
            LeaveCredit.objects.create(
                person=p,
                leave_type=LeaveType.VACATION,
                days=vl_days,
                as_of=date(2026, 1, 1),
                note="Beginning balance FY 2026",
            )
            LeaveCredit.objects.create(
                person=p,
                leave_type=LeaveType.SICK,
                days=sl_days,
                as_of=date(2026, 1, 1),
                note="Beginning balance FY 2026",
            )

        for p in eligible_people[:25]:
            req = LeaveRequest.objects.create(
                person=p,
                leave_type=random.choice([LeaveType.VACATION, LeaveType.SICK]),
                start_date=date(2026, 4, 10),
                end_date=date(2026, 4, 12),
                days=Decimal("3.0"),
                reason="Personal / family matter.",
                status=LeaveRequest.Status.APPROVED,
                decided_at=timezone.make_aware(datetime(2026, 4, 8, 9, 30)),
            )
            LeaveCredit.objects.create(
                person=p,
                leave_type=req.leave_type,
                days=Decimal("-3.0"),
                as_of=req.start_date,
                note=f"Deduction for approved leave #{req.id}",
                leave_request=req,
            )

        for p in eligible_people[25:35]:
            LeaveRequest.objects.create(
                person=p,
                leave_type=LeaveType.VACATION,
                start_date=date(2026, 11, 12),
                end_date=date(2026, 11, 14),
                days=Decimal("3.0"),
                reason="Scheduled family vacation.",
                status=LeaveRequest.Status.PENDING,
            )
