#!/bin/sh
# Nightly backup on the VPS: a database dump plus the uploaded documents, kept 14 days.
# Run on the server from cron (README "Run it on a DigitalOcean droplet", step 7):
#     sh /root/sanpascual-hr/scripts/vps_backup.sh
# Backups stay on the same droplet, so also turn on DigitalOcean's droplet backups,
# or copy /root/hr-backups somewhere else now and then.
set -e
cd "$(dirname "$0")/.."

dest="${BACKUP_DIR:-/root/hr-backups}"
keep_days="${BACKUP_KEEP_DAYS:-14}"
stamp=$(date +%Y-%m-%d_%H%M)
compose="docker compose -f docker-compose.prod.yml"

mkdir -p "$dest"
chmod 700 "$dest"

# As the owner role, so the dump includes the schema it owns. Custom format restores
# with pg_restore (README "Restore a backup").
$compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$dest/db_$stamp.dump"
$compose exec -T web tar czf - -C /data/media . > "$dest/media_$stamp.tar.gz"

find "$dest" -type f \( -name 'db_*.dump' -o -name 'media_*.tar.gz' \) -mtime +"$keep_days" -delete
echo "Backup $stamp: $(du -sh "$dest/db_$stamp.dump" | cut -f1) database, $(du -sh "$dest/media_$stamp.tar.gz" | cut -f1) uploads."
