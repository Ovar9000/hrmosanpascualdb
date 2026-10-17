#!/bin/sh
# Loads FAKE review data into the Railway deployment. Run it inside the web service from
# your PC (README "Run it on Railway"):
#     railway ssh -- sh scripts/railway_demo_setup.sh
#     railway ssh -- sh scripts/railway_demo_setup.sh --new-passwords   # reissue passwords
# Connects as the website's restricted role (data only), so it cannot change tables or
# the audit log. Safe to run again: existing people and demo passwords are kept.
set -e
cd /app

manage() { python manage.py "$@"; }
count() { manage shell -v 0 -c "$1" | tail -n 1; }

echo "==> Offices and roles"
manage seed_offices >/dev/null
manage setup_roles >/dev/null

if [ "$(count 'from hr.models import Person; print(Person.objects.count())')" = "0" ]; then
    echo "==> Loading about 420 fake people (takes a minute)"
    manage seed_fake >/dev/null
fi
echo "    $(count 'from hr.models import Person; print(Person.objects.count())') fake people loaded"

# create_demo_users and seed_demo_calendar refuse to run unless DEBUG is on. DJANGO_DEBUG=1
# applies to these one-off commands only; the website itself keeps DJANGO_DEBUG=0.
# Accepted for the time-limited, fake-data review (DECISIONS H4).
demo_users=$(count "from django.contrib.auth.models import User; print(User.objects.filter(username__startswith='demo_').count())")
if [ "$demo_users" = "0" ] || [ "$1" = "--new-passwords" ]; then
    echo "==> Demo accounts. Passwords are shown ONCE: send each one privately, never in a group chat."
    DJANGO_DEBUG=1 manage create_demo_users
else
    echo "==> $demo_users demo accounts already exist; passwords unchanged (add --new-passwords to reissue)"
fi

echo "==> Calendar sample events around today"
DJANGO_DEBUG=1 manage seed_demo_calendar | tail -n 1

echo "==> Org chart reporting lines (fills empty ones only)"
DJANGO_DEBUG=1 manage seed_reporting_lines | tail -n 1

echo "==> Salary actions: sample long careers, then the daily check"
DJANGO_DEBUG=1 manage seed_demo_salary_history | tail -n 1

manage verify_audit_chain >/dev/null && echo "==> Audit hash chain verifies. Done."
