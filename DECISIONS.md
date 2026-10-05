# Decisions and assumptions

Every assumption made while building the prototype. **Survey?** marks how likely the
HRMO survey is to change it:

- **Likely**: expected to change; built so it's easy to replace.
- **Maybe**: reasonable default, confirm with HRMO.
- **No**: technical or policy choice, unlikely to change.

---

## Survey findings (n=2, Oct 2026)

Responses from Municipal HRMO Head and Administrative Aide, plus follow-up interviews:

1. **Workforce Composition & Headcount:**
   - Total active personnel: ~414 to ~420.
   - Breakdown: ~94 permanent (Plantilla), ~28 casual, 12 elected (Mayor, Vice Mayor, 8 SB Members, ABC President, SK Federation President), ~280+ Job Order (JO) / Contract of Service (COS).
   - **JO/COS represents roughly two-thirds (67%) of the municipal workforce.**
2. **Current Operational Pain Points:**
   - Records are split across disconnected Excel and Google Sheets files: JO database, permanent database, service records, and leave credits.
   - Issues reported: duplicate employee profiles, conflicting entries across files, inaccurate leave balance computation, misplaced paper documents, and delayed reports.
3. **Legal & Document Reality:**
   - **Wet signatures and physical paper originals remain legally required.**
   - The system tracks physical archive locations (cabinet, drawer, folder); digital document uploads are digital scans/attachments, not paper replacements.
4. **Leave Approval Order (UNCONFIRMED):**
   - The Municipal Mayor (or authorized representative) signs **all** leave applications.
   - The exact step order between the two survey respondents differed (Supervisor recommendation vs HRMO credit certification vs Mayor approval). Built as a configurable approval chain.
5. **Leave Computation (UNVERIFIED):**
   - Leave credit computation formulas reported discrepancies.
   - No automatic monthly 1.25-day accrual. Credits are entered as baseline ledgers, and deduction occurs only upon final approved leave.
   - JO/COS generally do not earn paid leave; controlled explicitly via `Engagement.leave_eligible`.
6. **Attendance / DTR (Out of Scope):**
   - DTR is not strictly recorded in HRMO; pay is salary-based. No attendance module in Phase 1.
7. **Pass Slip (Deferred):**
   - Pass slip workflow: Employee → HRMO → Mayor's Office (or representative). Currently on a separate Excel sheet; deferred, but approval engine designed generically to support it.
8. **Parking Lot (To clarify later):**
   - Specific "Other" tasks mentioned by HRMO Head; Google Sheets leave and attendance coordination.

---

## Scope and data

| # | Decision | Survey? |
|---|---|---|
| D1 | Fake data only. Fake IDs always start with zeros (TIN `000-000-000-000`, SSS `00-0000000-0`, etc.). | No |
| D2 | Payroll computation is out of scope. Only payroll *inputs* (rate, salary grade, step, basis) are stored. | No |
| D3 | Lists of options (engagement types, statuses, leave types, document types) are defined in code TextChoices. | Maybe |
| D4 | One `Person` row per human, ever. Each appointment or contract is a separate `Engagement`, so history is kept, not overwritten (e.g. JO → Casual → Plantilla). | No |
| D5 | Plantilla fields (salary grade, step, item number) are blank columns on `Engagement`. Database rules make them required for plantilla and forbidden for all other types. | Maybe |
| D6 | Pay rate basis: monthly, daily, or lump sum (JO/COS daily rate). | Maybe |
| D7 | Project-based engagements link to a `Project`. Contractors' own workers are not tracked. | No |
| D8 | Elected officials are `Engagement`s of type Elected, with no plantilla fields. | Maybe |
| D9 | Each person has a fake employee number (`SP-00001`), used as their login username. | Maybe |
| D10 | Database indexes placed on `Person(last_name, first_name)` and `Person(employee_no)` for performance across 420+ records. | No |

---

## Roles, permissions, and security

| # | Decision | Survey? |
|---|---|---|
| R1 | Explicit Django permissions used for gating: `view_pay`, `view_ids`, `view_documents`, `approve_leave`, `view_hr_records`, `manage_hr_records`. | No |
| R2 | **System Admin (IT) isolation:** Manages accounts and system settings in `/admin/`. Non-superusers cannot see or edit any HR models in `/admin/`. System admins have no access to HR records screens. | No |
| R3 | **Privilege Escalation Prevention:** Only the emergency superuser can assign users to `HRMO Staff`, `Auditor`, `Executive`, or `Delegate` groups. System Admin cannot self-escalate into HR access roles. All account creations, password resets, and group changes are audit-logged. | No |
| R4 | **Data Minimization:**<br>- `view_ids`: HRMO, Auditor, and self only.<br>- `view_documents`: HRMO, Auditor, and self only.<br>- Office Heads: See document existence/status, not content.<br>- Executives & Delegates: See name, position, office, engagement type, and routed leave request (no IDs, no pay). | No |
| R5 | "Their office" = people with an **active** engagement in that office. Transferred staff are not visible to their past office heads. Roles stack (e.g. HRMO Head = Office Head + HRMO Staff). | Maybe |
| R6 | **Pay rate visibility (`view_pay`):** Strictly restricted to **HRMO Staff** and **Auditor**. Hidden from Office Heads, Office Staff, Executives, Delegates, and Employees. | No |
| R7 | **Executive Delegate:** Time-bound delegation via `Delegate` model (`principal`, `delegate`, `valid_from`, `valid_to`). Access ends automatically when delegation expires. | No |
| R8 | Emergency Superuser: Emergency maintenance only. Routine administration uses role-based accounts. | No |

---

## Leave

| # | Decision | Survey? |
|---|---|---|
| L1 | **Approval chain:** Configurable `ApprovalStep` per request type. Default seeded chain: 1) Supervisor recommends, 2) HRMO certifies credits, 3) Mayor or delegate approves. Marked **UNCONFIRMED**. | **Likely** |
| L2 | **Credit deduction:** Simple ledger (`LeaveCredit`). Deductions occur only upon final approval. Computation formulas marked **UNVERIFIED**. | **Likely** |
| L3 | **Leave eligibility:** Controlled by `Engagement.leave_eligible` (default True for Plantilla and Casual, False for others; editable by HRMO). Replaces strict type hardcoding. | No |

---

## Documents

| # | Decision | Survey? |
|---|---|---|
| F1 | Wet signatures and physical originals stay legally binding. `Document.original_on_file` (Boolean) tracks physical copy; physical cabinet/drawer location is mandatory. Digital scan uploads are optional. | No |
| F2 | Uploaded files live in a private volume, served only through permission-checked views. Max 10 MB, PDF/JPG/PNG. | Maybe |

---

## Accounts and authentication

| # | Decision | Survey? |
|---|---|---|
| A1 | Employee accounts receive a **random one-time password** with `must_change_password=True`. No shared default passwords. | Maybe |
| A2 | No email password reset. Password resets handled by HRMO issuing a new one-time password. | Maybe |
| A3 | Session timeout: 30 minutes idle. Login rate-limiting: 5 failed attempts per username+IP locks for 15 minutes. | Maybe |

---

## Import / export

| # | Decision | Survey? |
|---|---|---|
| X1 | Excel import is all-or-nothing: if any row fails, nothing is imported and the uploaded file is returned with an "Errors" column. Column mappings are configurable to accommodate differing legacy sheets. | Maybe |
| X2 | Duplicate person = same last name, first name, and birthdate (case-insensitive). | Maybe |

---

## Audit log

| # | Decision | Survey? |
|---|---|---|
| U1 | **Event coverage:** Logs who, action (`CREATE`, `UPDATE`, `DELETE`, `VIEW`, `EXPORT`, `LOGIN`, `LOGOUT`, `FAILED_LOGIN`, `ACCOUNT_LOCKOUT`, `PASSWORD_RESET`, `GROUP_CHANGE`, `PERMISSION_DENIED`), target entity, field diffs, IP address, timestamp, and office snapshot. `VIEW` is strictly logged for person detail, document downloads, and exports (not list directory pages). | No |
| U2 | **Database-level immutability:** Append-only enforced via PostgreSQL PL/pgSQL trigger (`audit_log_prevent_tampering`) blocking all `UPDATE`, `DELETE`, and `TRUNCATE` operations on `hr_auditlog`. In addition, `hr_app` database role has `UPDATE, DELETE, TRUNCATE` revoked. | No |
| U3 | **Atomic transaction binding:** Audit entries are committed in the same database transaction as the business operation. If audit logging fails, the entire transaction rolls back and the action fails. | No |
| U4 | **Data minimization & redaction:** Sensitive fields (`rate`, `tin`, `gsis_no`, `philhealth_no`, `pagibig_no`, `sss_no`, `password`) log field names only. Values and password hashes are **NEVER** stored in the audit changes diff. | No |
| U5 | **Trusted proxy IP extraction:** Client IP is resolved from `X-Forwarded-For` strictly when `REMOTE_ADDR` matches `TRUSTED_PROXIES`. Direct connections ignore spoofed proxy headers. | No |
| U6 | **Signal limitations & code prohibitions:**<br>- Django signals do **not** trigger on `QuerySet.update()`, `QuerySet.bulk_create()`, `QuerySet.bulk_update()`, raw SQL `cursor.execute()`, or direct `psql` queries.<br>- **Policy:** HR application code is forbidden from calling `.update()`, `.bulk_create()`, `.bulk_update()`, or raw SQL for mutating domain entities. All domain mutations must use model `.save()` or `.delete()`. An automated AST test verifies this in the test suite.<br>- **Dev DB reset:** Because `TRUNCATE` is blocked by the PostgreSQL trigger, `manage.py flush` will be rejected. Developers must use `docker compose down -v` to reset a local development database.<br>- **Hash chain assessment:** An optional SHA-256 hash chain (`prev_hash`, `row_hash`) is supported on `AuditLog`. **Recommendation:** We do *not* recommend relying on synchronous in-database hash chaining as the primary defense over PostgreSQL engine-level append-only triggers. In a high-concurrency web application, computing a strictly linear hash chain requires serializing database writes (`SELECT FOR UPDATE`), creating heavy lock contention. Database-level triggers combined with periodic external write-once log export (e.g. daily signed CSV archives or off-site syslog streaming) provide stronger immutability without crippling concurrent web write performance. | No |
| U7 | **Audit retention policy & no-purge design:** The web application cannot purge or truncate audit rows by design (enforced by PostgreSQL trigger and DB privilege revocation). Audit-log retention policy (e.g., retention window, active DB vs. cold storage archiving) is an open question to align with the municipal Data Privacy Officer (DPO). | Yes |
| U8 | **Nightly encrypted export of audit table:** Nightly encrypted export of the `hr_auditlog` table is automatically exported and streamed to LGU-held storage (e.g., external encrypted backup volume or isolated municipal share) to protect audit integrity even in the event of host failure or database corruption. | No |

---

## HRMO Data Entry & Record Management (Step 6)

| # | Decision | Survey? |
|---|---|---|
| S1 | **Race-safe employee number sequence:** Sequential employee numbers (`SP-00001` format) are generated atomically inside the creation transaction using an `EmployeeSequence` database row locked with `select_for_update()`. The sequence is excluded from audit signals to prevent unnecessary lock contention and audit noise. It re-syncs automatically to the highest existing number upon database seeding or `seed_fake --clear`. | No |
| S2 | **Lost-update protection via integer versioning:** Concurrent editing is prevented using an integer `version` field on `Person`. When saving edits, the row is locked with `select_for_update()` inside an atomic transaction. If the database `version` does not match the submitted `version`, the edit is rejected with a user-friendly conflict notice instead of silently overwriting concurrent changes. | No |
| S3 | **Record correction and non-deletion policy:** Erroneous records are corrected by editing. Historical appointments and statuses are retired by ending/deactivating engagements. Hard deletion is completely absent from user screens and reserved for emergency superuser administration in the Django admin. | No |
| S4 | **Data Privacy Officer (DPO) Questions regarding Correction, Erasure, and Retention:**<br>1. *Right to rectification (RA 10173):* What documentation or administrative approval is required before correcting historical personnel or service details, and does CSC or COA require a formal rectification certificate?<br>2. *Right to erasure vs. mandatory government archiving:* CSC and COA regulations mandate perpetual preservation of 201 files and service records. Under what exceptional conditions (e.g. invalid test entries or legally expunged records) may an emergency superuser purge a record?<br>3. *Retention schedule:* How long should active operational audit logs remain online in PostgreSQL before being archived to external encrypted cold storage? | Yes |
| S5 | **Duplicate person matching and limits:** Duplicate check compares `(last_name, first_name, middle_name, suffix, birthdate)` case-insensitively.<br>*Documented limitation:* Automated checks cannot detect duplicates when an employee changes surname upon marriage, when middle names are omitted or misspelled on legacy documents, or when two distinct individuals share identical names and birthdates. Such cases require manual verification by HRMO staff. | No |
| S6 | **Candidate CSC Form 212 (PDS) fields to review with HRMO:**<br>- Civil Service Eligibility (Career Service Professional/Sub-Professional, RA 1080 Board/Bar, Barangay Official Eligibility)<br>- Blood type, height (m), weight (kg)<br>- GSIS BP Number vs. Common Reference Number (CRN) vs. Policy Number<br>- Educational Background (Elementary, Secondary, Vocational, College, Graduate Studies)<br>- Emergency Contact Person, Relationship, and Mobile Number<br>- Civil Status (Single, Married, Widowed, Separated, Other)<br>- Citizenship (Filipino by birth / naturalization / dual citizenship)<br>- Residential Address vs. Permanent Address distinction | Yes |
| S7 | **Engagement workflows, overlap warnings, and rate field gating:**<br>- *Overlap warning:* Overlapping active engagements of the same type within the same office do not hard-block creation/renewal, but require the user to check an explicit warning acknowledgment (`confirm_overlap`) before saving.<br>- *Plantilla rules:* Plantilla appointments (`PERMANENT`, `COTERMINOUS`, `TEMPORARY`, `ELECTIVE`) require `salary_grade`, `step`, and `item_number`; non-plantilla engagements prohibit them.<br>- *Project-based:* `PROJECT_BASED` engagements require an associated project link; other types disallow project association.<br>- *Renewal & ending:* Ending sets `status=ended` with an `end_date` and optional audit note. Renewing pre-fills a new engagement record from the old one, retaining full historical continuity.<br>- *Field gating:* Pay rate and basis inputs are restricted to users with `hr.view_pay` (`HRMO Staff`, `Auditor`).<br>- *Lost-update protection:* Managed via integer `version` field on `Engagement` with `select_for_update()`. | No |

---

## Disaster recovery & backup plan

| # | Decision | Survey? |
|---|---|---|
| B1 | Nightly scheduled `pg_dump` of PostgreSQL database (schema, records, and append-only audit log). | No |
| B2 | Storage: Local server volume + secondary encrypted backup on separate physical storage or municipal network share. | No |
| B3 | Restoration drill: IT System Admin executes annual/quarterly dry-run restores in a sandboxed container to verify backup integrity. | No |
| B4 | Nightly encrypted export of the `hr_auditlog` table streamed to LGU-held storage. | No |

---

## Project Roadmap

- **Step 1:** Docker Compose scaffold, settings, auth, README, DECISIONS. *(Done)*
- **Step 2:** Data models, initial migrations, `seed_offices` (25 offices), unit tests. *(Done)*
- **Step 3:** Fake data seed command (151 people across engagement types), tests. *(Done)*
- **Step 3b:** Demo screens: responsive office directory and personnel list/detail. *(Done)*
- **Step 4A:** Model adjustments (`leave_eligible`, `original_on_file`, `Delegate`, indexes, admin isolation), migrations, tests. *(Done)*
- **Step 4B:** Scaled fake seed data (~420 people matching 94/28/12/280+ municipal composition), 25/page pagination. *(Done)*
- **Step 4C:** Role and permission system (`setup_roles` command, query/field gates, negative security tests). *(Done)*
- **Step 5:** Append-only audit log (DB trigger, view & export logging, auditor search screen). *(Done)*
- **Step 6:** HRMO data entry screens:
  - 6A: People (create, edit, race-safe sequence, lost-update, duplicate & age validation). *(Done)*
  - 6B: Engagements (create, edit, end, renew, overlap warning). *(Done)*
  - 6C: Documents (upload verification, signature check, metadata edit). *(Current)*
  - 6D: Demo user management command, DPO wrap-up.
- **Step 7:** Reports & analytics (demographics, tenure bands, contract expirations, CSC lists, payroll inputs).
- **Step 8:** Configurable leave approval workflow (steps, delegation, scan attachment, ledger deduction).
- **Step 9:** Excel import/export (configurable columns, row-by-row error report).
- **Step 10:** Mobile employee self-service (my profile, leave filing, one-time passwords).
