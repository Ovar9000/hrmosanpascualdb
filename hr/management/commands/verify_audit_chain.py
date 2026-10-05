"""Management command to verify the cryptographic SHA-256 hash chain of AuditLog.

Always a full scan from genesis. The result is stored in AuditChainStatus so /audit/
can show it without verifying on page load.
"""

from django.core.management.base import BaseCommand, CommandError
from hr.audit import run_and_record_verification


class Command(BaseCommand):
    help = "Verifies the cryptographic SHA-256 hash chain of the audit log and reports broken links."

    def add_arguments(self, parser):
        parser.add_argument(
            "--anchor",
            type=str,
            help="Expected anchor in format ID:HASH (fails if row hash is missing or different)",
        )

    def handle(self, *args, **options):
        anchor = options.get("anchor")
        result = run_and_record_verification(anchor=anchor, source="command")

        if result.is_valid:
            if anchor and result.anchor_verified:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Audit hash chain verified: {result.count} record(s) intact. "
                        f"Anchor '{anchor}' verified (tamper-evident)."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Audit hash chain verified: {result.count} record(s) intact (chain consistent). No broken links."
                    )
                )
        else:
            self.stderr.write(self.style.ERROR(f"Audit hash chain verification FAILED: {result.error_msg}"))
            raise CommandError(f"Broken audit chain detected at record #{result.broken_id}: {result.error_msg}")
