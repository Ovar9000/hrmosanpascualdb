"""Management command to display the latest audit anchor (id and row_hash with timestamp)."""

from django.core.management.base import BaseCommand
from hr.models import AuditLog


class Command(BaseCommand):
    help = "Prints the latest audit log anchor (ID and row_hash) with timestamp for external custody."

    def add_arguments(self, parser):
        parser.add_argument(
            "--raw",
            action="store_true",
            help="Output only the ID:HASH anchor string (useful for automated export scripts).",
        )

    def handle(self, *args, **options):
        latest = AuditLog.objects.order_by("-id").first()
        if not latest:
            self.stdout.write(self.style.WARNING("Audit log is empty. No anchor available."))
            return

        anchor_str = f"{latest.id}:{latest.row_hash}"
        if options.get("raw"):
            self.stdout.write(anchor_str)
            return

        self.stdout.write(self.style.SUCCESS("Latest Audit Log Anchor:"))
        self.stdout.write(f"  Audit ID:   {latest.id}")
        self.stdout.write(f"  Row Hash:   {latest.row_hash}")
        self.stdout.write(f"  Timestamp:  {latest.timestamp.isoformat()}")
        self.stdout.write(f"  Anchor:     {anchor_str}")
