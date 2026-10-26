# Changelog

The version in every page footer is `MAJOR.MINOR.PATCH`, worked out automatically at each deploy from the
commit messages since the last release in `VERSION` (`scripts/version.py`, DECISIONS H9):

- **PATCH** (1.1.**1**): only `fix:` (or untyped) commits since the last release.
- **MINOR** (1.**2**.0): at least one `feat:` commit. The patch number goes back to 0.
- **MAJOR** (**2**.0.0): a `feat!:` / `fix!:` commit or "BREAKING CHANGE" in a message, such as going live with
  real data.
- `docs:`, `chore:`, `test:` and similar commits change nothing users see, so the number stays.

To close a release (optional bookkeeping): set `VERSION` to the number the footer shows, rename the
"Unreleased" heading here to it, and tag the commit (`git tag v1.2.0`). Hover over the version in the footer
to see the exact commit that is running.

## Unreleased (next minor, 1.2.0)

Added:
- **Two-step login** with an authenticator app (Google Authenticator, Microsoft Authenticator, Aegis): no email or
  SMS needed. Optional for now: anyone turns it on under *username → Two-step login* (scan a QR code, type a
  code, keep ten recovery codes). `MFA_REQUIRED=1` makes HRMO Staff, Auditor, System Admin and superusers set it
  up at their next login. **Remember this browser** skips the code for 30 days on your own device
  (`MFA_REMEMBER_DAYS`); a password change or **Forget remembered browsers** ends it. Five wrong codes lock it for
  15 minutes. A lost phone is reset by the emergency superuser in `/admin/` or with `manage.py reset_mfa` (A5).

- The audit trail can be grouped by the person who acted (`/audit/?view=grouped`): one collapsible section
  per user, with their office, event count and time range, so events from people working at the same time
  no longer interleave. Filters and paging work in both views; the CSV export stays chronological (U1-U6).

- **Forgot password?** on the login page. The person asks online (or just goes to HRMO); HRMO checks a photo
  ID and issues a one-time password, shown once with a printable slip, from **Tools → Forgotten passwords** or
  the person's record. It lifts a login lockout, must be changed at first login and stops working after 24
  hours if unused (`RESET_PASSWORD_HOURS`). HRMO resets plain employee accounts only; accounts with a role or
  delegation stay with the emergency superuser (A2, R3).

Fixed:
- Paging through the audit trail dropped the From/To date filters.

## 1.1.1 (October 2026)

Fixed:
- System Admin could reset the password of an employee or delegate account in `/admin/` and log in as
  them, reaching that person's PDS, IDs and leave, or a delegate's view of every office. Every account
  that reaches HR records is now managed by the emergency superuser only (R3, A2).
- HRMO staff could change their own record: appointment and pay, leave and overtime credits, rating,
  trainings, SALN receipt, salary actions, and verify their own PDS. Another HRMO staff member now does
  these (R10).

## 1.1.0 (October 2026)

Added:
- Service records and certificates of employment, generated from the appointments and requested online or at
  the HRMO counter, including by former employees and heirs (CR1-CR6).
- Personal Data Sheet (CS Form No. 212) online, verified by HRMO, printable as the four-page form (PD1-PD7).
- Licence expiry and eligibility tracking, with qualification evidence for employee-applicants (EL1-EL3).
- Training hours per person per year, and one training recorded for everyone who attended (LD1-LD3).
- SPMS performance ratings per semester, filling in the step increment checklist (SP1-SP5).
- SALN filing tracker with reminders before April 30 (SN1-SN4).
- Compensatory overtime credits (COC) and compensatory time-off (CTO) in the leave module (CT1-CT5).
- The version in the page footer.

Changed:
- Emoji replaced with plain words and small monochrome status marks.

## 1.0.0 (October 2026)

The review demo as first deployed: personnel records, appointments and documents, HR calendar, org charts,
salary actions, reminder lists, required meetings, online leave (CS Form No. 6), Plantilla of Personnel,
vacancies and memos, roles and the audit trail.
