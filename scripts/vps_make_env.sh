#!/bin/sh
# Writes the server's .env with fresh random passwords (README "Run it on a DigitalOcean droplet").
# Run on the server, in the repo folder:   sh scripts/vps_make_env.sh hr.example.com
# The address is what people type in the browser, without https://. It must already
# point at this server (DNS A record, or <ip-with-dashes>.sslip.io).
# Refuses to overwrite an existing .env: the database keeps the passwords it was created with.
set -e
cd "$(dirname "$0")/.."

site="$1"
if [ -z "$site" ]; then
    echo "Usage: sh scripts/vps_make_env.sh <site address, e.g. hr.example.com>" >&2
    exit 1
fi
if [ -e .env ]; then
    echo ".env already exists; edit it by hand instead (nano .env)." >&2
    exit 1
fi

secret() { openssl rand -base64 64 | tr -d '/+=\n' | cut -c1-"$1"; }

umask 077
cat > .env <<EOF
# Server settings for docker-compose.prod.yml. Never commit or share this file.
SITE_ADDRESS=$site
DJANGO_SECRET_KEY=$(secret 50)

POSTGRES_DB=sanpascual_hr
POSTGRES_USER=hr_owner
POSTGRES_PASSWORD=$(secret 32)
APP_DB_USER=hr_app
APP_DB_PASSWORD=$(secret 32)

# Review demo: fake data only (DECISIONS D1, H3).
PROTOTYPE_FAKE_DATA_ONLY=1
# Names printed on CS Form No. 6 (DECISIONS L6). Blank prints an empty line.
LEAVE_FORM_CERTIFIER=
LEAVE_FORM_MAYOR=
EOF

echo "Wrote .env for https://$site (readable by root only)."
