"""Management command to verify the cryptographic SHA-256 hash chain of AuditLog."""

from django.core.management.base import BaseCommand, CommandError
from hr.audit import verify_chain


class Command(BaseCommand):
    help = "Verifies the cryptographic SHA-256 hash chain of the audit log and reports broken links."

    def add_arguments(self, parser):
        parser.add_argument(
            "--anchor",
            type=str,
            help="Expected anchor in format ID:HASH (fails if row hash is missing or different)",
        )
        parser.add_argument(
            "--reverify",
            action="store_true",
            help="Bypass cache and force full re-verification of the entire chain from genesis.",
        )

    def handle(self, *args, **options):
        anchor = options.get("anchor")
        reverify = options.get("reverify", False)
        # If anchor is provided, default to full verification unless specified
        if anchor:
            reverify = True

        result = verify_chain(anchor=anchor, reverify=reverify)
        is_valid = result.is_valid
        count = result.count
        error_msg = result.error_msg
        broken_id = result.broken_id
        anchor_verified = getattr(result, "anchor_verified", False)

        if is_valid:
            if anchor and anchor_verified:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Audit hash chain verified: {count} record(s) intact. "
                        f"Anchor '{anchor}' verified (tamper-evident)."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Audit hash chain verified: {count} record(s) intact (chain consistent). No broken links."
                    )
                )
        else:
            self.stderr.write(
                self.style.ERROR(
                    f"Audit hash chain verification FAILED: {error_msg}"
                )
            )
            raise CommandError(f"Broken audit chain detected at record #{broken_id}: {error_msg}")
