# Decisions and assumptions

Every assumption made while building the prototype. **Survey?** marks how likely the
HRMO survey is to change it:

- **Likely**: expected to change; built so it's easy to replace.
- **Maybe**: reasonable default, confirm with HRMO.
- **No**: technical or policy choice, unlikely to change.

When a survey answer changes something, update the row and note the date.

## Scope and data

| # | Decision | Survey? |
|---|---|---|
| D1 | Fake data only. Fake IDs always start with zeros (TIN `000-000-000-000`, SSS `00-0000000-0`, etc.). | No |
| D2 | Payroll computation is out of scope. Only payroll *inputs* (rate, salary grade, step) are stored. | No |
| D3 | Lists of options (engagement types, statuses, leave types, document types) are defined in code. Changing one is a one-line edit plus a migration. HRMO can't edit them in the admin. | Maybe |
| D4 | One `Person` row per human, ever. Each appointment or contract is a separate `Engagement`, so history is kept, not overwritten. | No |
| D5 | Plantilla fields (salary grade, step, item number) are blank columns on `Engagement`. A database rule makes them required for plantilla and forbidden for all other types. The item number is a plain field, not a separate "plantilla item" table that tracks vacancies. | Maybe |
| D6 | The pay rate has a basis: monthly, daily, or lump sum (JO/COS are usually paid daily). | Maybe |
| D7 | Project-based engagements link to a `Project`. Contractors' own workers are not tracked. | No |
| D8 | Elected officials are `Engagement`s of type Elected, with no plantilla fields. | Maybe |
| D9 | Each person has a fake employee number (`SP-00001`), used as their login username. | Maybe |

## Roles and access

| # | Decision | Survey? |
|---|---|---|
| R1 | Roles are Django groups plus object-level checks in one shared filter. | No |
| R2 | The IT system admin manages accounts and settings but is **not** a Django superuser, and has no access to HR records. The superuser account is for emergencies only. | No |
| R3 | "Their office" = people with a **current** (active) engagement in that office. Child offices and past staff are not included. | Maybe |
| R4 | Office staff are read-only: names, positions and contacts in their own office. No ID numbers, no documents. | Likely |
| R5 | Office heads see everything for their own office (except pay rates, see R6) and can approve leave. | Maybe |
| R6 | **Pay rate fields are visible only to HRMO staff and auditors.** Hidden from office heads, office staff, executives and employees. | Maybe |
| R7 | One person can hold several roles (e.g. an office head is also an employee); permissions add up. | No |
| R8 | Until roles exist (step 4), the demo screens are limited to Django staff users. | No (temporary) |

## Leave

| # | Decision | Survey? |
|---|---|---|
| L1 | **Approval is one step** (the office head of the employee's current office, or an executive), and nobody approves their own request. The approval step lives in one replaceable function. **Likely to change after the survey** to the CSC-style chain: HR certification of credits → head recommendation → authorized official approval. | **Likely** |
| L2 | Leave credits are a simple ledger (rows of +/− days per leave type). HRMO enters balances, and approved leave subtracts from them. No automatic monthly 1.25-day earning, no special rules. Kept simple until the survey. | **Likely** |
| L3 | Leave credits only for Plantilla and Casual engagements. | Maybe |

## Documents

| # | Decision | Survey? |
|---|---|---|
| F1 | Kept simple until the survey: one file attached to a person **or** an engagement, a type, a status (received / approved / filed), and a free-text physical location (e.g. "Cabinet 3, Drawer 2"). No versioning, no checklists. | **Likely** |
| F2 | Files live in a private volume, served only through permission checks. Max 10 MB, PDF/JPG/PNG. | Maybe |

## Accounts

| # | Decision | Survey? |
|---|---|---|
| A1 | HRMO creates employee accounts. Each gets a **random one-time password**, shown once to HRMO, and **must change it on first login**. | Maybe |
| A2 | No password reset by email (no mail server). A forgotten password is handled by HRMO issuing a new one-time password. | Maybe |
| A3 | Session timeout is 30 minutes idle. Login locks for 15 minutes after 5 failed attempts per username + IP. | Maybe |

## Import / export

| # | Decision | Survey? |
|---|---|---|
| X1 | Excel import is all-or-nothing: if any row fails, nothing is imported and the uploaded file comes back with an "Errors" column. | Maybe |
| X2 | Duplicate person = same last name, first name, and birthdate. | Maybe |

## Audit

| # | Decision | Survey? |
|---|---|---|
| U1 | Every create, update, and delete is logged with old and new values. Views of person records are also logged. | No |
| U2 | Append-only at the DB level: a trigger rejects UPDATE/DELETE/TRUNCATE, and the app DB role also loses those privileges. **Limit:** a PostgreSQL superuser can still drop the trigger. That can't be prevented inside the database; protect the superuser password and keep backups. | No |

## Technical

| # | Decision | Survey? |
|---|---|---|
| T1 | Django 5.2 LTS (security support to April 2028) instead of 6.x, to outlive the contract. | No |
| T2 | Bootstrap 5 files stored in the repo: no Node build step, works on an offline office server. | No |
| T3 | Two DB roles: the owner (migrations, tests) and the app (website). The web container only receives the app password via environment. In dev, the code bind mount still exposes `.env` inside containers; drop the mount on a real server. | No |
| T4 | Login IP for rate limiting = `REMOTE_ADDR`. Behind a reverse proxy, the axes proxy settings must be configured. | No |
| T5 | English UI, Asia/Manila timezone, dates shown as MM/DD/YYYY. | Maybe |
| T6 | Dev server (`runserver`) only in Phase 1. Gunicorn + static file serving come with deployment. | No |
| T7 | Separate git repo. The only remote is the private `Ovar9000/hrmosanpascualdb` on GitHub. Never push to the old `2nd-sem-2nd-year` remote. | No |

## Build order

Step 3b (basic office directory and people list for a demo) comes right after the fake
data seed, before roles and audit.
