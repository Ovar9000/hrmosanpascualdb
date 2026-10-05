from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0008_auditlog_request_id_auditlog_session_key_hash_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- PostgreSQL requires UPDATE privilege on at least one column to execute
            -- SELECT ... FOR UPDATE (used during atomic hash chain locking).
            -- Grant UPDATE exclusively on column 'id' to hr_app so row locks can be acquired,
            -- while the PL/pgSQL trigger trg_hr_auditlog_no_update_delete continues to block
            -- all actual row mutations (UPDATE, DELETE, TRUNCATE) at the database engine level.
            DO $grant$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hr_app') THEN
                    GRANT UPDATE (id) ON hr_auditlog TO hr_app;
                END IF;
            END $grant$;
            """,
            reverse_sql="""
            DO $revoke$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hr_app') THEN
                    REVOKE UPDATE (id) ON hr_auditlog FROM hr_app;
                END IF;
            END $revoke$;
            """,
        ),
    ]
