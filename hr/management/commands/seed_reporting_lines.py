"""Fills empty org chart reporting lines from the fake data so a demo shows real charts
(DEV ONLY, DECISIONS O5).

Per office: the head is the Mayor / Vice Mayor or the department head item (…-HD-01);
plantilla staff at salary grade 15 and up report to the head and act as section chiefs; everyone
else on the chart, and every JO / COS / project-based worker, is shared out among the section chiefs
(or reports to the head when there are none). Only empty lines are filled, so HRMO's own edits
survive a rerun. Offices without a clear head, like the Sangguniang Bayan members, are left alone.
Runs with auditing off, like the other seeders.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from hr.audit import disable_audit
from hr.models import Engagement, Office

HEAD_TITLES = ("Municipal Mayor", "Municipal Vice Mayor")
CHIEF_MIN_GRADE = 15


def _head(active):
    for eng in active:
        if eng.position_title in HEAD_TITLES or (eng.item_number or "").endswith("-HD-01"):
            return eng
    return None


class Command(BaseCommand):
    help = "Fills empty reporting lines in the fake data for org chart demos (DEV ONLY)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_reporting_lines is for demos only (DJANGO_DEBUG=1).")
        filled = offices = 0
        with disable_audit(), transaction.atomic():
            for office in Office.objects.order_by("name"):
                active = list(
                    office.engagements.filter(status=Engagement.Status.ACTIVE)
                    .select_for_update()
                    .order_by("-salary_grade", "position_title", "pk")
                )
                head = _head(active)
                if head is None:
                    continue
                offices += 1
                chiefs = [
                    e for e in active
                    if e.pk != head.pk and e.type == Engagement.Type.PLANTILLA and (e.salary_grade or 0) >= CHIEF_MIN_GRADE
                ]
                others = [e for e in active if e.pk != head.pk and e not in chiefs]
                plan = [(e, head) for e in chiefs]
                plan += [(e, chiefs[i % len(chiefs)] if chiefs else head) for i, e in enumerate(others)]
                for eng, supervisor in plan:
                    if eng.reports_to_id is None:
                        eng.reports_to = supervisor
                        eng.version += 1
                        eng.save(update_fields=["reports_to", "version", "updated_at"])
                        filled += 1
        self.stdout.write(f"Filled {filled} reporting lines in {offices} offices.")
