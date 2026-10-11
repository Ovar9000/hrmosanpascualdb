"""Re-applies the audit-table privilege limits to the configured app role.

0003 and 0009 named the role "hr_app" literally, so a deployment that set APP_DB_USER to
another name silently skipped the REVOKE. This reads APP_DB_USER (passed to the "manage"
service) and fails loudly if that role does not exist.

It also revokes the UPDATE (id) grant from 0009: chain appends now take an advisory lock
instead of SELECT ... FOR UPDATE on the last row, so the app role needs no UPDATE at all.
"""

import os
from django.db import migrations

DEFAULT_APP_ROLE = "hr_app"


def _role_exists(cursor, role):
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
    return cursor.fetchone() is not None


def restrict_app_role(apps, schema_editor):
    role = os.environ.get("APP_DB_USER", "").strip()
    with schema_editor.connection.cursor() as cursor:
        if not role:
            # Outside docker compose (no APP_DB_USER): restrict the default role if it exists.
            if not _role_exists(cursor, DEFAULT_APP_ROLE):
                return
            role = DEFAULT_APP_ROLE
        elif not _role_exists(cursor, role):
            raise RuntimeError(
                f"APP_DB_USER role '{role}' does not exist, so hr_auditlog privileges cannot be "
                "restricted. Create the role (docker/db-init) or fix APP_DB_USER before migrating."
            )
        quoted = schema_editor.quote_name(role)
        cursor.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON hr_auditlog FROM {quoted}")
        cursor.execute(f"REVOKE UPDATE (id) ON hr_auditlog FROM {quoted}")


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0010_auditchainstatus"),
    ]

    operations = [
        migrations.RunPython(restrict_app_role, migrations.RunPython.noop),
    ]
