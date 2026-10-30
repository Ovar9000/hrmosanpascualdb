# Changelog

The version in every page footer is `MAJOR.MINOR.PATCH`, worked out automatically at each deploy from the
commit messages since the last release in `VERSION` (`scripts/version.py`, DECISIONS H9):

- **PATCH** (1.1.**1**): only `fix:` (or untyped) commits since the last release.
- **MINOR** (1.**2**.0): at least one `feat:` commit. The patch number goes back to 0.
- **MAJOR** (**2**.0.0): a `feat!:` / `fix!:` commit or "BREAKING CHANGE" in a message, such as going live with
  real data.
- `docs:`, `chore:`, `test:` and similar commits change nothing users see, so the number stays.

Releases are cut by `python scripts/deploy.py`, which also pushes and deploys: it sets `VERSION`, renames the
"Unreleased" heading here to the new number and date, and tags the commit `vX.Y.Z`. So write each change
under "Unreleased" as you make it. Hover over the version in the footer to see the exact commit that is running.

## Unreleased

Added:
- **Official email register** (optional module `official_email`, HR Desk → Leaving the LGU): every gov.ph
  account on GovMail with its office, who holds it, who approved it and when its password was last reset. When a
  holder leaves the office, by separation, end of contract or transfer, the account shows as a password reset to
  do (a personal account: to close), counted on the HR Desk. Recording the reset hands it over to someone in that
  office and keeps the history. A separation in progress lists the accounts to hand over on the last day; offices
  without an account are listed with a suggested address; Excel export (EM1-EM5).

## 1.4.1 (October 30, 2026)

Fixed:
- **Reporting lines**: HRMO staff can no longer change who they themselves report to on an office's
  reporting-lines page. Their own row shows their supervisor as text, and another HRMO staff member sets it,
  as with every other part of one's own record (R10).
- **Image library updated** (Pillow 13): the version in use had known security flaws. Uploads and the org
  chart picture work as before.

## 1.4.0 (October 28, 2026)

Added:
- **Appointment papers**: every plantilla, casual and contractual appointment now has a **Papers** page (person
  record → Papers) to fill in the nature of appointment, vice, and the signing, oath and assumption dates, and
  prints **CS Form No. 33** (salary in words, publication and HRMPSB lines from the vacancy for the item), the
  **Oath of Office**, the **Certificate of Assumption to Duty** and a **position description** (AP1-AP4).
- **Report on Appointments Issued** for any month, printed for the CSC Field Office or as Excel (Tools →
  Reports, AP5).
- **Job order and contract of service papers**: print one contract, or tick many and print them one per page
  (Tools → Papers), with the no-employer-employee-relationship clauses of the 2017 joint circular; and the
  semestral **JO/COS list** for the CSC (JC1-JC4).
- **Salary schedules, NOSA and NOSI**: paste the LGU's salary table; every plantilla appointment then gets a
  Notice of Salary Adjustment (print all at once, or Excel), and HRMO can update the stored salaries once the
  NOSAs are signed. A step increment under review prints its Notice of Step Increment with the amounts (NS1-NS4).
- **Pass slips**: employees file one before leaving the office, HRMO notes it and the Mayor approves it (order
  set by `PASS_SLIP_STEPS`), then they press **I'm leaving now** and **I'm back**. HRMO sees who is out right now
  and a monthly log with personal time added up per person (PS1-PS5).
- **Separation and clearance**: start one from the person's record; HRMO records each clearance signature
  (Treasurer, Accountant, GSO, office head, HRMO), prints the clearance form, sees a terminal leave estimate, and
  completing it ends the appointment as resigned, retired or terminated (SE1-SE5).
- **Awards and recognition** (PRAISE): record an award for many people at once, or from an issued loyalty
  case, and print a certificate (AW1-AW3).
- **Individual Development Plans**: employees write theirs, their office head reviews it, and HRMO tracks who
  has one by office (ID1-ID3).
- **Workforce profile**: people in active service by sex, appointment type, office, age, length of service, and
  persons with disability, solo parents and indigenous groups from the PDS, for the GAD plan (WF1-WF2).
- Pass slips, development plans, awards and the workforce profile are **switched off** until HRMO asks for them
  (`HR_MODULES`, MD1-MD3).
- **Plantilla items**: every item the Sangguniang Bayan created is now a record HRMO can add, mark unfunded or
  abolish, with the history of who held it (HR Desk → Plantilla items, PI1-PI4).

Changed:
- The **Tools** menu is now short: a link to the new **HR Desk**, which groups every tool by task (hiring and
  appointments, requests, leave and time, pay and benefits, and so on) with counts of what is waiting, plus
  only the items that need attention now (DK1-DK3).
- Printed appointment papers follow the **2025 ORAOHRA**: CS Form No. 33-A while the CSC regulates the LGU,
  33-B from the date set in `CSC_ACCREDITED_ON`; CS Form No. 4 and the ROAI (CS Form No. 2) are now Revised
  2025. Casuals no longer get CS Form No. 33: under the 2025 rules they go on the Plantilla of Casual
  Appointment (CS Form No. 34), not built yet (AP1).

Fixed:
- **Plantilla of Personnel**: an item whose holder retired or resigned no longer drops off the report when no
  vacancy has been drafted for it; it shows as vacant until filled or abolished (PI3).
- **Two-step login**: logging out and straight back in (the usual way to try it after setup) refused the code
  the app still showed as "not right" and counted it towards the 15-minute lock. A code still works only once,
  but now the page says it was just used and to wait for the next one, and it no longer counts as a wrong code.
  Codes pasted with an unusual space are accepted (A5).
- **Two-step login**: when the phone's and the server's clocks disagree (a PC whose clock had drifted 93 seconds
  refused every code), the page now says the code is right but for a time N seconds ahead or behind, and what
  to fix, instead of "not right". Such codes are still refused, but no longer count towards the lock (A5).

## 1.3.0 (October 27, 2026)

Added:
- The **Mayor's Office structure chart** now reads like HRMO's own: the Mayor's staff in a row just below the
  Mayor's box, each division with its chief, its sections under the chief and a column of positions under
  each section, every position under the one it reports to. Boxes are filled with the legend colour: red
  for a vacancy proposed for creation, green for an existing filled-up position, yellow elected, blue local
  special bodies. It follows HRMO's new six-division structure (CDD, CAPRD, GSD, IMSD, PSD, SDRD). The PDF
  and PowerPoint split it into slides of three divisions ("Part 1 of 2"), each still 16:9 (O7). On a server
  that already has the old structure, run `manage.py seed_structure --replace` once to load it (this
  discards admin edits to the Mayor's Office structure).

Fixed:
- **Org charts**: the outer boxes of the office tree were cut off at both edges, and the wide Mayor's Office
  structure was hard to take in. The Mayor's Office structure now puts its divisions in rows (3 + 3) either
  side of the line down from the Mayor, so on a laptop or desktop screen it fits whole at about 60–70% instead
  of 30%; the PNG picture does the same, and the PDF and PowerPoint keep their slides of three divisions (O7).
  Every chart now uses the full width of the window and opens zoomed out so it fits whole. Zoom in and out with
  **−** / **+** (up to 200%), Ctrl + scroll or a trackpad pinch; **Fit** and **100%** jump back. Drag with the
  mouse to move around a zoomed-in chart. The chart scrolls inside its own frame, which can no longer widen
  the page.

## 1.2.0 (October 26, 2026)

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

- The version in the footer is worked out from the commit messages at each deploy, so it no longer lags
  behind new features; hover over it for the exact build (H9).

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
