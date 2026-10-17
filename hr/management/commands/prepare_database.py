"""Creates (or updates) the restricted app role, then applies migrations.

For hosts where docker/db-init cannot run, such as Railway's managed PostgreSQL. Run it
as the database owner (DB_USER/DB_PASSWORD = owner) with APP_DB_USER and APP_DB_PASSWORD
set: the same role setup as docker/db-init/01-app-role.sh, then `migrate`. Safe to run on
every deploy: an existing role only gets its password and grants re-applied.
"""

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from psycopg import sql


class Command(BaseCommand):
    help = "Creates the restricted app DB role (APP_DB_USER) if needed, then runs migrate. Run as the DB owner."

    def handle(self, *args, **options):
        app_user = os.environ.get("APP_DB_USER", "").strip()
        app_password = os.environ.get("APP_DB_PASSWORD", "")
        if not app_user or not app_password:
            raise CommandError("Set APP_DB_USER and APP_DB_PASSWORD (the login the website uses).")
        if len(app_password) < 16:
            raise CommandError("APP_DB_PASSWORD is too short: use at least 16 random characters.")

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user, current_database()")
            owner, db = cursor.fetchone()
        if owner == app_user:
            raise CommandError(
                f"Connected as '{owner}', the app role itself. Run this with the owner login in "
                "DB_USER/DB_PASSWORD, so the website's role stays unable to change tables."
            )

        role, owner_id, db_id = sql.Identifier(app_user), sql.Identifier(owner), sql.Identifier(db)
        grants = [
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db_id, role),
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role),
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(owner_id, role),
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}"
            ).format(owner_id, role),
            # Tables that already exist, e.g. the role was recreated after migrating.
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(role),
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role),
        ]
        connection.ensure_connection()
        with transaction.atomic():
            # psycopg's own cursor, not Django's: Django's query log would record the password.
            with connection.connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [app_user])
                exists = cursor.fetchone() is not None
                verb = "ALTER" if exists else "CREATE"
                cursor.execute(
                    sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(role, sql.Literal(app_password))
                )
                for statement in grants:
                    cursor.execute(statement)
                self._restrict_audit_table(cursor, role)
        self.stdout.write(
            f"{'Updated' if exists else 'Created'} app role '{app_user}' (data access only) "
            f"in database '{db}', owner '{owner}'."
        )

        # Migration 0011 also reads APP_DB_USER and restricts the audit table on a new database.
        call_command("migrate", interactive=False, verbosity=options["verbosity"])
        connection.ensure_connection()
        with connection.connection.cursor() as cursor:
            self._restrict_audit_table(cursor, role)
        self._salary_check()
        self.stdout.write(self.style.SUCCESS("Database ready."))

    def _salary_check(self):
        """Opens due salary increase and loyalty cases right after each deploy (DECISIONS SA5).
        A failure here is reported but never blocks the deploy: the tracker reruns it on its own."""
        from hr.salary_actions import run_check

        try:
            created, updated = run_check(source="deploy")
        except Exception as exc:  # noqa: BLE001 - the database itself is ready; only the check failed
            self.stderr.write(f"Salary actions check skipped: {exc}")
        else:
            self.stdout.write(f"Salary actions check: {created} new case(s), {updated} updated.")

    @staticmethod
    def _restrict_audit_table(cursor, role):
        """The audit log is append-only for the app role (DECISIONS U2); the grants above re-add UPDATE/DELETE."""
        cursor.execute("SELECT to_regclass('public.hr_auditlog') IS NOT NULL")
        if cursor.fetchone()[0]:
            cursor.execute(sql.SQL("REVOKE UPDATE, DELETE, TRUNCATE ON hr_auditlog FROM {}").format(role))
