"""Seeds the 25 official municipal offices of San Pascual.

Idempotent: safe to run repeatedly.
"""

from django.core.management.base import BaseCommand
from hr.models import Office

OFFICES_DATA = [
    # Legislative branch
    {
        "name": "Office of the Vice Mayor",
        "acronym": "OVM",
        "branch": Office.Branch.LEGISLATIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Vice Mayor",
        "location": "Legislative Building, 2nd Floor",
        "contact": "ovm@sanpascual.gov.ph",
        "parent_name": None,
    },
    {
        "name": "Office of the Sangguniang Bayan",
        "acronym": "SB",
        "branch": Office.Branch.LEGISLATIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Presiding Officer / Sangguniang Bayan",
        "location": "Legislative Building, Session Hall",
        "contact": "sb@sanpascual.gov.ph",
        "parent_name": None,
    },
    {
        "name": "Office of the Secretary to the Sangguniang Bayan",
        "acronym": "OSSB",
        "branch": Office.Branch.LEGISLATIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Secretary to the Sangguniang Bayan",
        "location": "Legislative Building, 1st Floor",
        "contact": "ossb@sanpascual.gov.ph",
        "parent_name": "Office of the Sangguniang Bayan",
    },
    # Executive branch - Core / Statutory Departments
    {
        "name": "Office of the Municipal Mayor",
        "acronym": "MO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Mayor",
        "location": "Municipal Hall, 2nd Floor, Executive Wing",
        "contact": "mayor@sanpascual.gov.ph",
        "parent_name": None,
    },
    {
        "name": "Office of the Municipal Administrator",
        "acronym": "OMA",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Administrator",
        "location": "Municipal Hall, 2nd Floor",
        "contact": "admin@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Human Resources and Management Officer (HRMO)",
        "acronym": "HRMO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal HRMO",
        "location": "Municipal Hall, Ground Floor",
        "contact": "hrmo@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Municipal Civil Registrar",
        "acronym": "MCR",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Civil Registrar",
        "location": "Municipal Hall, Ground Floor",
        "contact": "mcr@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Treasurer",
        "acronym": "MTO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Treasurer",
        "location": "Municipal Hall, Ground Floor, Treasury Wing",
        "contact": "treasury@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Business Permits and Licensing Office",
        "acronym": "BPLO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.SPECIAL,
        "head_title": "BPLO Head",
        "location": "Municipal Hall, Ground Floor",
        "contact": "bplo@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Budget Officer",
        "acronym": "MBO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Budget Officer",
        "location": "Municipal Hall, 2nd Floor",
        "contact": "budget@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Accountant",
        "acronym": "MACCO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Accountant",
        "location": "Municipal Hall, 2nd Floor",
        "contact": "accounting@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Assessor",
        "acronym": "MASSO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Assessor",
        "location": "Municipal Hall, Ground Floor",
        "contact": "assessor@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Planning and Development Coordinator",
        "acronym": "MPDC",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Planning and Development Coordinator",
        "location": "Municipal Hall, 2nd Floor",
        "contact": "mpdc@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Engineer",
        "acronym": "MEO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Engineer",
        "location": "Engineering Building, Ground Floor",
        "contact": "engineering@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Agriculturist",
        "acronym": "OMAgr",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Agriculturist",
        "location": "Agriculture & Livelihood Center",
        "contact": "agriculture@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Environment and Natural Resources Officer",
        "acronym": "MENRO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Environment and Natural Resources Officer",
        "location": "Municipal Compound, MENRO Building",
        "contact": "menro@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Municipal Tourism Office",
        "acronym": "MTO-Tour",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.SPECIAL,
        "head_title": "Municipal Tourism Officer",
        "location": "Municipal Hall, Ground Floor",
        "contact": "tourism@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Municipal Disaster Risk Reduction and Management Office (MDRRMO)",
        "acronym": "MDRRMO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "MDRRM Officer",
        "location": "MDRRMO Operations Center, Poblacion",
        "contact": "mdrrmo@sanpascual.gov.ph / 911-SANPASCUAL",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Municipal Social Welfare and Development Office (MSWDO)",
        "acronym": "MSWDO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Social Welfare and Development Officer",
        "location": "Social Services Center, Ground Floor",
        "contact": "mswdo@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Health Officer",
        "acronym": "MHO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.DEPARTMENT,
        "head_title": "Municipal Health Officer",
        "location": "Rural Health Unit (RHU) Main Building",
        "contact": "mho@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    # Special offices
    {
        "name": "Office of the Municipal Persons with Disability Affairs Officer",
        "acronym": "PDAO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.SPECIAL,
        "head_title": "PDAO Officer",
        "location": "Social Services Center, 1st Floor",
        "contact": "pdao@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Public Employment Service Manager",
        "acronym": "PESO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.SPECIAL,
        "head_title": "PESO Manager",
        "location": "Municipal Hall, Ground Floor",
        "contact": "peso@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Youth Development Officer",
        "acronym": "MYDO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.SPECIAL,
        "head_title": "Municipal Youth Development Officer",
        "location": "Municipal Hall, 2nd Floor",
        "contact": "youth@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    {
        "name": "Office of the Municipal Population Officer",
        "acronym": "MPO",
        "branch": Office.Branch.EXECUTIVE,
        "type": Office.Type.SPECIAL,
        "head_title": "Municipal Population Officer",
        "location": "Municipal Hall, Ground Floor",
        "contact": "population@sanpascual.gov.ph",
        "parent_name": "Office of the Municipal Mayor",
    },
    # Institution
    {
        "name": "San Pascual Polytechnic College",
        "acronym": "SPPC",
        "branch": Office.Branch.OTHER,
        "type": Office.Type.INSTITUTION,
        "head_title": "College Administrator / President",
        "location": "SPPC Main Campus, San Pascual",
        "contact": "sppc@sanpascual.edu.ph",
        "parent_name": None,
    },
]


class Command(BaseCommand):
    help = "Seeds the 25 official municipal offices of San Pascual."

    def handle(self, *args, **options):
        self.stdout.write("Seeding municipal offices...")
        created_count = 0
        updated_count = 0

        # First pass: create/update office rows without parents
        for item in OFFICES_DATA:
            office, created = Office.objects.update_or_create(
                name=item["name"],
                defaults={
                    "acronym": item["acronym"],
                    "branch": item["branch"],
                    "type": item["type"],
                    "head_title": item["head_title"],
                    "location": item["location"],
                    "contact": item["contact"],
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        # Second pass: wire up parent foreign keys
        for item in OFFICES_DATA:
            if item["parent_name"]:
                parent = Office.objects.filter(name=item["parent_name"]).first()
                if parent:
                    Office.objects.filter(name=item["name"]).update(parent=parent)

        total = Office.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded offices. Created: {created_count}, Updated: {updated_count}. Total in DB: {total}."
            )
        )
