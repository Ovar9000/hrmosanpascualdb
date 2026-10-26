# San Pascual HR Records (prototype)

HR records system prototype for the Municipal Human Resource Management Office
(HRMO), Municipality of San Pascual.

> **Fake data only.** Never load real personal data into this prototype. Fake ID
> numbers always start with zeros (e.g. `00-0000000-0`).
>
> This is enforced by database constraints, not just the form. Setting
> `PROTOTYPE_FAKE_DATA_ONLY=0` only turns off the form check; the database still
> rejects every ID that doesn't start with `00`. Accepting real IDs needs DPO
> approval, real-ID format validation, and a deliberate migration that removes the
> constraints. See item 1 of the Go-Live Checklist in `DECISIONS.md`.

Stack: Django 5.2 LTS · PostgreSQL 17 · Bootstrap 5 (stored in the repo, no CDN) · Docker Compose · openpyxl.

## Setup (local)

Requires Docker Desktop (or Docker Engine + Compose v2) and git.

```powershell
git clone <this repo> sanpascual-hr
cd sanpascual-hr
copy .env.example .env
git config core.hooksPath .githooks
```

The last line turns on the repo's git hooks, which keep the footer's version number up to date after
every commit, checkout or pull (DECISIONS H9). Without it the local footer shows the last release.

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
| Prepare a demo (clean fake data, demo accounts, checks) | `pwsh -File scripts\demo_setup.ps1 -Reset` |
| Run tests | `docker compose run --rm manage python manage.py test` |
| Any management command | `docker compose run --rm manage python manage.py <command>` |
| Create employee accounts (preview first) | `docker compose run --rm manage python manage.py provision_accounts --dry-run` |
| Reset two-step login (lost phone, after an ID check) | `docker compose run --rm manage python manage.py reset_mfa <username> --reason "..."` |
| Django deploy checklist | `docker compose run --rm manage python manage.py check --deploy` |
| Wipe the database (fake data only!) | `docker compose down -v` |

The init script in `docker/db-init/` only runs on an **empty** database volume.
If you change `APP_DB_USER` / `APP_DB_PASSWORD` after the first start, run
`docker compose down -v` to recreate the database.

## Run it on a DigitalOcean droplet (review demo)

A public copy for the HRMO officer to try during the review period (until
**2026-11-13**, see `DECISIONS.md` H3 and H7). **Fake data and demo accounts only.**
One small Linux server ("droplet") runs everything with `docker-compose.prod.yml`:

| Service | What it is | Holds |
|---|---|---|
| `db` | PostgreSQL, not reachable from outside the server | All data |
| `migrate` | Runs at first start and on every update: creates/updates the `hr_app` login, migrates, then stops | The database owner password |
| `web` | The website (gunicorn), reachable only through Caddy | Only the limited `hr_app` password, plus the uploads volume |
| `caddy` | The only public part: HTTPS certificate and forwarding to `web` | Its certificate |

Cost: the **$12/month** droplet (2 GB RAM). DigitalOcean bills by the hour up to that
amount; a sign-up credit covers the first days. Droplet backups add 20%.

### 1. Project

In the DigitalOcean control panel, **New Project**:

- **Project name:** `San Pascual HR review`
- **Environment type:** Staging (or Development)
- **What it's for:** a web application / trying it out (any choice is fine)

Skip "Assign resources" for now; the droplet is added to the project in step 3.

### 2. An SSH key on your PC

The key is how you log in to the server; it replaces a password. In PowerShell:

```powershell
ssh-keygen -t ed25519 -C "sanpascual-hr"
```

Press Enter to accept the file name, and set a passphrase. Then show the public half
(the part that is safe to paste into DigitalOcean):

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

### 3. Create the droplet

**Create → Droplets**, then:

1. **Region:** Singapore (closest to the Philippines).
2. **Image:** Ubuntu 24.04 (LTS) x64.
3. **Size:** Basic → Regular (SSD) → **$12/mo, 2 GB / 1 CPU / 50 GB**.
4. **Authentication:** SSH Key → **New SSH Key**, paste the line from step 2, name it.
5. Optional: **Enable automated backups** (adds 20%; recommended).
6. **Hostname:** `sanpascual-hr`. **Project:** the one from step 1.
7. **Create Droplet.** Copy its **IPv4 address** when it is ready, e.g. `203.0.113.10`.

### 4. A web address

HTTPS certificates need a name, not a bare IP. Either:

- **Free, no setup:** use the IP with dashes plus `.sslip.io`, e.g. `203-0-113-10.sslip.io`.
  It already points at your droplet.
- **Your own domain:** add a DNS **A record**, e.g. `hr.example.com` → the droplet IP.

### 5. Install and start

Log in from PowerShell (answer `yes` the first time):

```powershell
ssh root@203.0.113.10
```

Everything from here runs **on the server**. Get the code (if the repository is
private, git asks for your GitHub username and a
[fine-grained token](https://github.com/settings/personal-access-tokens) with read-only
"Contents" access as the password):

```bash
git clone https://github.com/Ovar9000/hrmosanpascualdb.git /root/sanpascual-hr
```

```bash
cd /root/sanpascual-hr
```

The server deploys `main`: `scripts/vps_update.sh` pulls the branch that is checked out, so
stay on `main`. Then prepare the server (Docker, firewall, swap,
automatic security updates, key-only SSH):

```bash
sh scripts/vps_setup.sh
```

Write the settings file with fresh random passwords (use your address from step 4):

```bash
sh scripts/vps_make_env.sh 203-0-113-10.sslip.io
```

Build and start (the first build takes a few minutes):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

```bash
docker compose -f docker-compose.prod.yml logs migrate
```

The log must end with `Database ready.` Then open `https://<your address>/healthz/`:
it should show `ok`. If the page does not load, `docker compose -f
docker-compose.prod.yml logs caddy` shows certificate problems (usually the address
does not point at the droplet yet).

### 6. Load the fake data and demo accounts

```bash
docker compose -f docker-compose.prod.yml exec -u app web sh scripts/railway_demo_setup.sh
```

Same script and output as on Railway (step 6 there): copy the `demo_*` usernames and
**one-time passwords, shown once**. Add `--new-passwords` to reissue them.

### 7. Nightly backup and morning salary check

The server clock is UTC, so 21:30 and 22:00 are 05:30 and 06:00 in the Philippines:

```bash
cat > /etc/cron.d/sanpascual-hr <<'EOF'
30 21 * * * root sh /root/sanpascual-hr/scripts/vps_backup.sh >> /var/log/hr-backup.log 2>&1
0 22 * * * root cd /root/sanpascual-hr && docker compose -f docker-compose.prod.yml exec -T web python manage.py check_salary_actions >> /var/log/hr-salary.log 2>&1
EOF
```

Run the backup once by hand to check it works: `sh scripts/vps_backup.sh`. Backups go
to `/root/hr-backups` (14 days kept) on the same droplet, so they do not survive losing
the droplet: that is what DigitalOcean's droplet backups are for.

### 8. Check the client IP and give access

Same as Railway steps 7 and 8 below: log in as `demo_auditor`, and in **Audit log** your
login must show your public IP. A `172.30.0.x` address means the proxy setting is
wrong (`DECISIONS.md` H8): stop and fix it before inviting anyone. Then send each
account and its one-time password **privately**.

### During the review

- **Code updates:** push to the branch, then on the server `sh scripts/vps_update.sh`
  (pulls, rebuilds, migrates, restarts the website).
- **Locked out after 5 wrong passwords:**
  `docker compose -f docker-compose.prod.yml exec -u app web python manage.py axes_reset`
- **Logs:** `docker compose -f docker-compose.prod.yml logs --tail 100 web`
- **Status:** `docker compose -f docker-compose.prod.yml ps`

### Restore a backup

Replaces the current data with a backup (`<stamp>` is the date in the file names):

```bash
docker compose -f docker-compose.prod.yml stop web
docker compose -f docker-compose.prod.yml exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < /root/hr-backups/db_<stamp>.dump
docker compose -f docker-compose.prod.yml run --rm -T --no-deps --entrypoint "" web tar xzf - -C /data/media < /root/hr-backups/media_<stamp>.tar.gz
docker compose -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.prod.yml up -d
```

`migrate` re-applies the `hr_app` grants and the audit-log restriction to the restored data.

### After the review: stop the costs (2026-11-13)

**Droplet → Destroy → Destroy this Droplet** (also delete its backups and snapshots if
offered). A droplet that is only powered off is still billed. Then check **Billing**
shows nothing running.

## Run it on Railway (review demo)

The earlier option, if Railway accepts your payment method. Use one host, not both.

A public copy for the HRMO officer to try during the review period (until
**2026-11-13**, see `DECISIONS.md` H1–H6). **Fake data and demo accounts only.**
Railway's screens change from time to time; if a button has moved, look for the
same word nearby.

You will end up with three services in one Railway project:

| Service | What it is | Holds |
|---|---|---|
| `Postgres` | The database (Railway manages it) | All data |
| `migrate` | Runs once per deploy: creates the `hr_app` login and the tables, then stops | The database owner password |
| `web` | The website | Only the limited `hr_app` password, plus the uploads volume |

### 1. Account and plan

1. Sign in at <https://railway.com> with your GitHub account.
2. Choose the **Hobby** plan (about $5 a month, card needed; usage above the
   included amount is billed). Check the current price at <https://railway.com/pricing>.
   For this app expect roughly $5–10 a month in total.
3. Optional but wise: in your workspace's **Usage** settings, set a spending limit.

### 2. Prepare two secrets on your PC

Run this twice and keep both lines somewhere private (not in the repo, not in chat):

```powershell
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(50))"
```

- The first is the **secret key**.
- The second is the **app database password** (used by `migrate` and `web`).

### 3. Project, database and services

1. **New Project → Deploy from GitHub repo →** `Ovar9000/hrmosanpascualdb`. Railway builds
   the `Dockerfile`. Rename this service to `web` (click its name). It will fail to start
   until step 5: that is expected.
2. **+ Create → Database → PostgreSQL.** Keep the name `Postgres`.
3. **+ Create → GitHub Repo →** the same repo again. Rename it to `migrate`. In its
   **Settings**:
   - **Custom Start Command:** `python manage.py prepare_database`
   - **Restart Policy:** Never
   - Do **not** generate a domain or add a volume for it.

Both app services deploy the `main` branch. Merge the Railway changes into `main` first,
or set **Settings → Source → Branch** on both to the branch with these changes.

### 4. Variables for `migrate`

In `migrate` → **Variables → Raw Editor**, paste and fill in the secret key and app
password (the `${{...}}` parts are Railway references and are filled in for you):

```
DJANGO_SECRET_KEY=<secret key>
DB_HOST=${{Postgres.PGHOST}}
DB_PORT=${{Postgres.PGPORT}}
DB_NAME=${{Postgres.PGDATABASE}}
DB_USER=${{Postgres.PGUSER}}
DB_PASSWORD=${{Postgres.PGPASSWORD}}
APP_DB_USER=hr_app
APP_DB_PASSWORD=<app database password>
```

Deploy it. Its log must end with `Database ready.` Then it stops; that is normal.

### 5. Variables, volume and domain for `web`

1. `web` → **Settings → Networking → Generate Domain**. If it asks for a port, enter
   `8080`. Note the address, e.g. `hrmo-review.up.railway.app`.
2. `web` → **Variables → Raw Editor** (use your own address):

   ```
   DJANGO_SECRET_KEY=<secret key>
   DJANGO_DEBUG=0
   DJANGO_HTTPS=1
   DJANGO_ALLOWED_HOSTS=hrmo-review.up.railway.app
   DJANGO_CSRF_TRUSTED_ORIGINS=https://hrmo-review.up.railway.app
   TRUSTED_PROXIES=100.0.0.0/8
   CLIENT_IP_HEADER=X-Real-IP
   PORT=8080
   DB_HOST=${{Postgres.PGHOST}}
   DB_PORT=${{Postgres.PGPORT}}
   DB_NAME=${{Postgres.PGDATABASE}}
   DB_USER=hr_app
   DB_PASSWORD=<app database password>
   ```

   `web` never gets the owner password (`DECISIONS.md` H2).
3. Add a volume to `web` (right-click the service, or **+ Create → Volume**) with mount
   path `/data/media`. Uploaded scans live there and survive redeploys.
4. `web` → **Settings → Deploy → Healthcheck Path:** `/healthz/`
5. Redeploy `web`. Opening `https://<your address>/healthz/` should show `ok`.

### 6. Load the fake data and demo accounts

Install the Railway command-line tool once, then connect it to the project:

```powershell
npm install -g @railway/cli
```

```powershell
railway login
```

```powershell
railway link
```

Pick the project and the `web` service. Then:

```powershell
railway ssh -- sh scripts/railway_demo_setup.sh
```

This loads offices, roles, ~420 fake people, calendar samples, org chart reporting
lines (empty ones only), a few long careers for the Salary Actions tracker and sample
lapsed contracts, retirements and a sample quarterly meeting for the trackers, and prints the
`demo_*` usernames with **one-time passwords, shown once**. Copy them now. Run it again
later safely; add `--new-passwords` to reissue passwords.

### 6b. Optional: run the salary actions check every morning

The salary check already runs after every deploy (in `migrate`) and whenever HRMO or the
Auditor opens the tracker or the calendar and the last run is more than 20 hours old, so
this step is optional. To have cases ready before
anyone opens the page:

1. **+ Create → GitHub Repo →** the same repo. Rename it `salary-check`. No domain, no volume.
2. **Settings → Custom Start Command:** `python manage.py check_salary_actions`
3. **Settings → Cron Schedule:** `0 22 * * *` (Railway uses UTC: 06:00 in the Philippines).
4. **Variables:** the same as `web` (it connects as `hr_app`, data only).

Each run logs `Salary actions check: N new case(s), M updated.` and is safe to repeat.

### 7. Check the client IP setting (important)

Log in as `demo_auditor` (you will be asked to set a new password), open **Audit log**,
and find your own login. Its IP must be your public IP (search "what is my IP" to
compare). If it shows a `100.x.x.x` address instead, the proxy setting is wrong and
every user would share one lockout counter: stop and fix `TRUSTED_PROXIES`
(`DECISIONS.md` H5) before inviting anyone.

### 8. Give the HRMO officer access

Send the address and their account (e.g. `demo_hrmo`, plus others to try other roles)
with its one-time password **in a private message**. Tell them: fake data only; they
must set a new password at first login and should not reuse a real one; unused
one-time passwords expire after 14 days.

### During the review

- **Code updates:** pushing to the deployed branch redeploys both services. If the
  update has new migrations, wait for `migrate` to log `Database ready.`, then redeploy `web`.
- **Locked out after 5 wrong passwords:** `railway ssh -- python manage.py axes_reset`
- **Logs:** each service's **Deployments → View logs**.

### After the review: stop the costs (2026-11-13)

**Project → Settings → Danger → Delete project.** This deletes the database, the
uploads volume and both services, and stops the charges. Then check
**Workspace → Billing** shows nothing running, and cancel the Hobby plan there if you
no longer need it.

## Security choices

| Topic | What we do | Why |
|---|---|---|
| Secrets | All in `.env` (git-ignored). The app refuses to start without `DJANGO_SECRET_KEY`. | No credentials in the repo. |
| Passwords | Django's default PBKDF2-SHA256 hashing (salted, ~1M iterations). Minimum 10 characters; common and all-numeric passwords are rejected. | Vetted default; Django upgrades stored hashes automatically when it raises the iteration count. |
| Login rate limiting | `django-axes`: 5 failed attempts for the same username + IP → locked for 15 minutes. Attempts are recorded and visible in the admin. Behind a reverse proxy this needs `TRUSTED_PROXIES` (lockouts use the same client-IP resolver as the audit log). | Stops password guessing without letting an attacker lock out a real user from every device. |
| Session timeout | 30 minutes idle (the timer resets on every request), and the session also ends when the browser closes. | Shared office PCs and phones left on desks. |
| Database roles | The website connects as `APP_DB_USER`, which can read and write data but cannot change tables. Migrations and tests run as the owner role in the one-shot `manage` container. The DB port is not published to the host. | Limits damage if the web app is compromised, and lets the audit table be made append-only (step 5). |
| Uploaded files | Stored in a private volume, never at a public URL; served only through permission-checked views. | HR documents are sensitive. |
| HTTPS | One switch: `DJANGO_HTTPS=1` turns on secure cookies, HTTP→HTTPS redirect, HSTS, and trusts `X-Forwarded-Proto` from the reverse proxy. | Ready for a TLS proxy without code changes. |
| Reverse proxy IP | `TRUSTED_PROXIES` in `.env` (comma-separated IPs/subnets). `X-Forwarded-For` is inspected ONLY when `REMOTE_ADDR` matches a trusted proxy, and read right to left so entries the client wrote itself are ignored. Behind a proxy that sets the client address in its own header (Railway: `X-Real-IP`), set `CLIENT_IP_HEADER` to read that header from trusted proxies instead. | Prevents IP spoofing in audit logs and rate-limiting while capturing the real client IP behind Caddy/Nginx. |
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
- Django inspects `REMOTE_ADDR`: if and only if it matches an entry or CIDR block in `TRUSTED_PROXIES`, the true client IP is resolved from the `X-Forwarded-For` header for audit logs and rate-limiting, taking the rightmost address that is not itself a trusted proxy. Direct or untrusted connections ignore spoofed proxy headers.

Then in `.env`: `DJANGO_HTTPS=1`, add the hostname to `DJANGO_ALLOWED_HOSTS`, and
add `https://<hostname>` to `DJANGO_CSRF_TRUSTED_ORIGINS`, and set `DJANGO_DEBUG=0`.
Run the Docker image's default command (gunicorn, with WhiteNoise serving `/static/`)
rather than the compose file's development `runserver`.

For quick **fake-data** demos on a phone over the LAN only, set `WEB_BIND=0.0.0.0`
and add the PC's LAN IP to `DJANGO_ALLOWED_HOSTS`.

## Project docs

- [`DECISIONS.md`](DECISIONS.md): every assumption, and which ones the HRMO survey may change.
- [`docs/DEMO.md`](docs/DEMO.md): presentation run sheet (rehearsal, walkthrough, questions to ask).
- [`docs/HANDOVER.md`](docs/HANDOVER.md): scope, what production needs, and who should own it.
