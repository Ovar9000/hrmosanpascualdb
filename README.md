# San Pascual HR Records (prototype)

HR records system prototype for the Municipal Human Resource Management Office
(HRMO), Municipality of San Pascual.

> **Fake data only.** Never load real personal data into this prototype. Fake ID
> numbers always start with zeros (e.g. `00-0000000-0`).

Stack: Django 5.2 LTS · PostgreSQL 17 · Bootstrap 5 (stored in the repo, no CDN) · Docker Compose · openpyxl.

## Setup (local)

Requires Docker Desktop (or Docker Engine + Compose v2) and git.

```powershell
git clone <this repo> sanpascual-hr
cd sanpascual-hr
copy .env.example .env
```

Edit `.env` and replace every `CHANGE-ME`. To generate a secret key:

```powershell
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Start everything:

```powershell
docker compose up --build
```

This starts PostgreSQL, runs migrations once (the `manage` service), then starts the
site at <http://localhost:8000>.

Create your first admin account (in a second terminal):

```powershell
docker compose run --rm manage python manage.py createsuperuser
```

## Common commands

| Task | Command |
|---|---|
| Start / stop | `docker compose up` / `docker compose down` |
| Run tests | `docker compose run --rm manage python manage.py test` |
| Any management command | `docker compose run --rm manage python manage.py <command>` |
| Django deploy checklist | `docker compose run --rm manage python manage.py check --deploy` |
| Wipe the database (fake data only!) | `docker compose down -v` |

The init script in `docker/db-init/` only runs on an **empty** database volume.
If you change `APP_DB_USER` / `APP_DB_PASSWORD` after the first start, run
`docker compose down -v` to recreate the database.

## Security choices

| Topic | What we do | Why |
|---|---|---|
| Secrets | All in `.env` (git-ignored). The app refuses to start without `DJANGO_SECRET_KEY`. | No credentials in the repo. |
| Passwords | Django's default PBKDF2-SHA256 hashing (salted, ~1M iterations). Minimum 10 characters; common and all-numeric passwords are rejected. | Vetted default; Django upgrades stored hashes automatically when it raises the iteration count. |
| Login rate limiting | `django-axes`: 5 failed attempts for the same username + IP → locked for 15 minutes. Attempts are recorded and visible in the admin. | Stops password guessing without letting an attacker lock out a real user from every device. |
| Session timeout | 30 minutes idle (the timer resets on every request), and the session also ends when the browser closes. | Shared office PCs and phones left on desks. |
| Database roles | The website connects as `APP_DB_USER`, which can read and write data but cannot change tables. Migrations and tests run as the owner role in the one-shot `manage` container. The DB port is not published to the host. | Limits damage if the web app is compromised, and lets the audit table be made append-only (step 5). |
| Uploaded files | Stored in a private volume, never at a public URL; served only through permission-checked views. | HR documents are sensitive. |
| HTTPS | One switch: `DJANGO_HTTPS=1` turns on secure cookies, HTTP→HTTPS redirect, HSTS, and trusts `X-Forwarded-Proto` from the reverse proxy. | Ready for a TLS proxy without code changes. |
| Reverse proxy IP | `TRUSTED_PROXIES` in `.env` (comma-separated IPs/subnets). `X-Forwarded-For` is inspected ONLY when `REMOTE_ADDR` matches a trusted proxy. | Prevents IP spoofing in audit logs and rate-limiting while capturing the real client IP behind Caddy/Nginx. |
| Other defaults kept on | CSRF protection, clickjacking protection (`X-Frame-Options: DENY`), `nosniff`, HTML auto-escaping, parameterised SQL (Django ORM). | |

### HTTPS is required for phone self-service

Employees will log in from their own phones over the office Wi-Fi or mobile data.
Over plain `http://`, their password and session cookie travel unencrypted and can
be read by anyone on the same network. **Do not let employees use the site
without HTTPS**, even on a local office server.

On a local office server, the simplest route is a reverse proxy such as
[Caddy](https://caddyserver.com/) in front of the `web` container:

- With a public domain name: Caddy obtains a free Let's Encrypt certificate automatically.
- LAN only (no public domain): use Caddy's `tls internal` (or your own CA) and
  install the root certificate on staff phones, or phones will show warnings.

#### Reverse proxy IP handling (`TRUSTED_PROXIES`)

When Caddy sits in front of the application, client connections terminate at Caddy, and requests reach Django from Caddy's IP (e.g., `127.0.0.1` or a Docker network bridge IP like `172.18.0.x`).

- Configure `TRUSTED_PROXIES` in `.env` with Caddy's IP address or Docker subnet (e.g. `TRUSTED_PROXIES=127.0.0.1,::1,172.16.0.0/12`). Defaults to localhost (`127.0.0.1,::1`).
- Django inspects `REMOTE_ADDR`: if and only if it matches an entry or CIDR block in `TRUSTED_PROXIES`, the true client IP is resolved from the `X-Forwarded-For` header for audit logs and rate-limiting. Direct or untrusted connections ignore spoofed proxy headers.

Then in `.env`: `DJANGO_HTTPS=1`, add the hostname to `DJANGO_ALLOWED_HOSTS`, and
add `https://<hostname>` to `DJANGO_CSRF_TRUSTED_ORIGINS`. Also replace `runserver`
with a production server (gunicorn) and set `DJANGO_DEBUG=0`. That deployment
setup is not part of Phase 1.

For quick **fake-data** demos on a phone over the LAN only, set `WEB_BIND=0.0.0.0`
and add the PC's LAN IP to `DJANGO_ALLOWED_HOSTS`.

## Project docs

- [`DECISIONS.md`](DECISIONS.md): every assumption, and which ones the HRMO survey may change.
