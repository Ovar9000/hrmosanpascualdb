"""Management command to verify the cryptographic SHA-256 hash chain of AuditLog."""

from django.core.management.base import BaseCommand, CommandError
from hr.audit import verify_chain


class Command(BaseCommand):
    help = "Verifies the cryptographic SHA-256 hash chain of the audit log and reports broken links."

    def handle(self, *args, **options):
        is_valid, count, error_msg, broken_id = verify_chain()
        if is_valid:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Audit hash chain verified: {count} record(s) intact. No broken links."
                )
            )
        else:
            self.stderr.write(
                self.style.ERROR(
                    f"Audit hash chain verification FAILED: {error_msg}"
                )
            )
            raise CommandError(f"Broken audit chain detected at record #{broken_id}: {error_msg}")
