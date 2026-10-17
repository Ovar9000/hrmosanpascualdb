"""Daily check for step increments and loyalty awards (DECISIONS SA1, SA5).

Schedule it once a day (Railway: a cron service running this command; see README). Safe to run
any number of times: it only opens a case once per milestone and never touches cases HRMO has
started. Connects as the app role, so it can run in the web service too.
"""

from django.core.management.base import BaseCommand
from hr.salary_actions import run_check


class Command(BaseCommand):
    help = "Opens, re-dates and closes step increment and loyalty award cases."

    def handle(self, *args, **options):
        created, updated = run_check(source="command")
        self.stdout.write(f"Salary actions check: {created} new case(s), {updated} updated.")
