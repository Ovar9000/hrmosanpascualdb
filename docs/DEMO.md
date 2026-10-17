# Demo run sheet

A 25-minute walkthrough of the San Pascual HR prototype for HRMO, with
Planning / Budget, the DPO and IT if they attend. **Fake data only.** Every name,
ID number and document in the demo database is generated.

---

## Before the day (rehearsal, about 45 minutes)

1. **Build a clean demo database and run the tests:**

   ```powershell
   pwsh -File scripts\demo_setup.ps1 -Reset -RunTests
   ```

   The script asks you to type `RESET`, loads 25 offices and 420 fake people,
   creates the demo accounts, fills the calendar around today, and checks the site and the audit chain. It ends with `Ready`.
2. **Copy the demo passwords onto paper.** The script shows them once.
3. **Log in once with each demo account and set its demo password** (10+ characters).
   Every account is forced to do this at first login. Keep one account untouched
   if you want to show the first-login screen live (scene 5). Write the new
   passwords on the same paper, never in the repo.
4. **Pick your example people** while logged in as `demo_hrmo`:
   - someone with two or three appointments (filter **Type: Plantilla**, open a few
     people, look for a Job Order → Casual → Plantilla history);
   - someone in the **Office of the Municipal Treasurer**: `demo_head` heads that office.
5. **Prepare one browser window per account.** Sessions are per browser, so use
   private windows or separate browser profiles: HRMO, Office Head, Auditor, Projects.
6. **Walk through every scene once** with these notes open.
7. Keep the **system map** page open for architecture questions (deployment, schema,
   who sees what).

### On the day

```powershell
pwsh -File scripts\demo_setup.ps1
```

Without `-Reset` the script keeps the data and the demo passwords you set. It only
starts the stack and runs the checks. Wait for `Ready`. Each run also refreshes the sample calendar items around that day.

**From a phone on the same Wi-Fi:** add `-Lan` and follow what it prints. This runs
over plain HTTP, so it is only acceptable with demo accounts and fake data. Don't use
it on public Wi-Fi.

---

## Demo accounts

| Account | Role | What it shows |
|---|---|---|
| `demo_hrmo` | HRMO Staff | Full records work: add, edit, appointments, documents |
| `demo_head` | Office Head, Municipal Treasurer | Own office only, no pay or IDs |
| `demo_exec` | Executive (Mayor's office) | Names, positions, offices only |
| `demo_auditor` | Auditor | Read-only everything, audit trail |
| `demo_projects` | Project Maintainer | Projects only, no personnel |
| `demo_admin` | System Admin (IT) | `/admin/` accounts only, no HR data |
| `demo_employee` | Employee | Own record only (the script prints which person) |

---

## The walkthrough

### 1. Opening (2 min)

- **The problem, from the October survey:** records split across separate Excel
  and Google Sheets files, duplicate profiles, conflicting entries, misplaced paper,
  delayed reports.
- **What this prototype does:** one record per person for their whole career,
  paper originals stay the legal master (the system records where they're filed),
  and every view and change is logged in an audit trail nobody can edit.
- Say it plainly: **this runs on fake data only.**

### 2. HRMO daily work (6 min) · `demo_hrmo`

1. **Personnel list:** 420 people. Search a surname, then filter by
   **Type: Job Order** and by office.
2. **Open your example person:** the appointment history from JO to Plantilla,
   documents with their cabinet and drawer, fake government IDs, pay rates.
   *Point:* a promotion adds a new appointment; the old one is kept, never overwritten.
3. **Add Person:**
   - The employee number is assigned automatically.
   - Type a real-looking TIN such as `123-456-789-000` → *"Prototype accepts fake ID
     numbers only (must start with 00)."*
   - Re-enter an existing person's full name and birthdate → *"… already exists
     (Employee #SP-…)"*. This is the duplicate-profile problem from the survey.
4. **Add Appointment** to someone: choose Plantilla without a salary grade → refused.
   Add a second active appointment of the same type in the same office → the overlap
   warning asks for confirmation.
5. **Documents:** upload `demo_files\sample_scan.pdf` → accepted. Upload
   `demo_files\renamed_text_file.pdf` → *"File content is not a valid PDF document…"*
   (the server reads the file itself, not the name). **Replace** a scan → the old one is
   marked **Superseded** and kept.

### 3. The HR calendar (2 min) · `demo_hrmo`

1. **Calendar:** this month's interviews, deadlines, meetings, holidays and staff leave.
   Each entry starts with a word for its kind, so it reads without relying on colour.
2. **Click a day number** → everything on that day, in full, grouped by kind.
3. **Go to December** → Dec 31 shows one entry, **"314 contract ends"**, instead of 314
   badges. Click it → the full list, sorted by office. *Point:* contract renewals can be
   planned from here.
4. **Filters:** untick everything except *Contract ends* and pick an office.
5. **+ on any day** adds an event (interview, deadline, holiday, meeting).
6. *Optional:* narrow the window or open it on a phone → the grid becomes a list.
7. **`demo_head`** sees only the Treasurer's Office: its staff's leave, starts and ends, plus
   events for everyone.

### 3b. Org charts (2 min) · `demo_hrmo`, then `demo_employee`

1. **Org Chart** in the menu → the municipal office tree with each head and headcount.
   Click the **Treasurer's Office** → its staff chart. JO and COS staff are counted under
   the chart, not drawn.
2. **Export → PowerPoint** and open it: drag a box, the lines follow. **Export → PDF / PNG**
   for printing or chat.
3. **Edit reporting lines** → change who one person reports to → Save. The chart redraws;
   the change is in the Audit Trail.
4. Log in as **`demo_employee`** → **My Office**: "You", your supervisor, the office head
   and your coworkers. Names and positions only; other offices are refused.

### 4. Two people editing at once (2 min) · `demo_hrmo`, two tabs

1. Open the same person's **Edit Details** in two tabs.
2. Change the mobile number in tab 1 and save.
3. Change the address in tab 2 and save → *"This record was modified by another user
   while you were editing it."* Nothing was overwritten. Refresh and redo the change.

### 5. Who sees what (4 min)

1. **`demo_head`:** only Treasurer's Office staff. Contacts are visible; birthdate,
   address, IDs, salary grade and pay are hidden. Documents show status and cabinet
   only, not the file.
   - Copy a person's address bar from the HRMO window (someone in another office) and
     paste it here → **Not found**.
   - *Point:* hiding things on the page isn't the protection. The server checks every
     request, so changing a URL or editing the page with Inspect Element gets you nothing.
2. **`demo_exec`:** names, positions, offices and appointment types only.
3. **`demo_employee`** (the account you left untouched): first login forces a new
   password, then the employee sees only their own record, including their own IDs.

### 6. Projects (3 min)

1. **`demo_projects`** lands on **Projects**. There is no Personnel menu at all.
2. **New Project:** "Coastal Road Phase 2", Ongoing, dates this year, funding reference
   `FAKE-CAF-2026-101`. The funding reference is required.
3. Open it: headcount is shown, names are not.
4. **`demo_hrmo`:** add a Project-based appointment on that project. Then set an end date
   after the project ends → refused: *"Ask the Project Maintainer to extend the project
   first."*
5. *Point:* defining and funding a project can sit with a different office from hiring
   into it. Ask the Planning / Budget questions below.

### 7. Audit trail (3 min) · `demo_auditor`

1. **Audit Trail**, filter user `demo_hrmo`: every change from scenes 2–6 with
   before/after values. Government IDs and pay show `[REDACTED]`, never the value.
2. **Re-verify all** → the hash chain is intact.
3. **Export CSV.**
4. *Point:* rows can only be added, never edited or deleted, not even by IT. The DPO
   keeps a short anchor code off the server, so tampering can be proven later.

### 8. IT administrator limits (1 min) · `demo_admin`

1. `/admin/`: user accounts are there; personnel records are not.
2. Edit a user: HRMO Staff, Auditor and the other HR roles aren't in the role list.
   Only the emergency superuser can assign them.

### 9. Employee accounts in bulk (1 min, terminal)

```powershell
docker compose run --rm manage python manage.py provision_accounts --dry-run --office MTO
```

The dry run lists who would get an account (18 in the Treasurer's Office) and creates
nothing. Explain the real run: one printed slip per person with a one-time password,
forced change at first login, expires after 14 days unused.

### 10. Close (2 min)

- **Next on the roadmap:** reports (Step 7), leave filing and approval (Step 8),
  Excel import (Step 9), employee phone self-service (Step 10).
- Ask the questions below and write the answers down.

---

## Questions to ask

**HRMO**
- What is the exact leave approval order: supervisor, HRMO certification, Mayor? Who
  signs when the Mayor is away? (L1, unconfirmed)
- Which Excel sheets should be imported first? Can we get a copy with the names and
  IDs blanked out to design the import?
- Which Personal Data Sheet fields do you actually need? (S6)

**Planning / Budget** (P4)
- When a project starts, which office records it, and which confirms the money exists?
- What document proves funding, and what does its number look like?
- Should JO / COS workers paid from a project also be linked to it?
- When a project ends, should its contracts end automatically?

**DPO**
- How long must the audit log stay online before it is archived? (U7, S4)
- Is storing mistyped usernames in the lockout table acceptable? (S9)
- Who keeps the audit anchor, and where?

**IT**
- Which machine hosts it? Is there a UPS and a locked room? A domain name for HTTPS?
- Where can backups go off the server?

---

## Likely questions, honest answers

| Question | Answer |
|---|---|
| Can we start using it now? | Not yet. It is a prototype on fake data. Before real data: DPO approval, HTTPS, real ID number validation, backups (Go-Live Checklist in `DECISIONS.md`). |
| Where is leave filing? | Step 8, after HRMO confirms the approval order. |
| Can it read our Excel files? | Step 9. We need sample sheets with the personal data blanked out. |
| Can employees use their phones? | Step 10, and only once HTTPS is set up. |
| What if two HR staff edit the same record? | Scene 4: the second save is stopped, and nothing is lost. |
| Can someone get in using Inspect Element? | Every request is checked on the server, and the test suite replays those attacks. Before go-live, a penetration test is still recommended. |
| Can IT see our records? | No. IT manages accounts only (scene 8), and everything they do is logged. |

---

## If something goes wrong

| Problem | Fix |
|---|---|
| Locked out after 5 wrong passwords | `docker compose run --rm manage python manage.py axes_reset` |
| Forgot a demo password | `docker compose run --rm manage python manage.py changepassword demo_hrmo` |
| Logged out mid-demo | Sessions end after 30 minutes idle. Log in again. |
| Page has no styling | `docker compose up -d`, then refresh |
| Site not loading | `docker compose ps`, then `docker compose logs web` |
| Data got messy during rehearsal | `pwsh -File scripts\demo_setup.ps1 -Reset` (new demo passwords too) |

**Don't:** type real names or ID numbers (even "just to test"), promise a go-live date,
or use the phone/LAN mode on public Wi-Fi.
