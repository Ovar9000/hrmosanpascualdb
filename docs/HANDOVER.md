# Scope and handover note

For the Municipal HRMO Head, Municipality of San Pascual.
Status as of October 2026. **Prototype, fake data only.**

---

## 1. What was built

An HR records prototype for HRMO, running on synthetic data for about 420 people across 25 offices.

| Area | What it does |
|---|---|
| Personnel records | One record per person for their whole career; every appointment, renewal and end is kept, never overwritten. Duplicate-person and fake-ID checks. |
| Appointments and documents | Plantilla, casual, JO, COS, project-based and elected appointments. Document scans with the paper original's cabinet and drawer recorded; replaced scans are kept. |
| Projects | Projects with a required funding reference. A separate Project Maintainer role defines projects; HRMO hires onto them; contracts must fit within the project's dates. |
| HR calendar | Leave, contract starts and ends, project dates, interviews, deadlines and holidays, filtered by role. Busy days collapse to one line ("314 contract ends"). |
| Org charts | The municipal office tree and a staff chart per office (JO, COS and project-based staff are counted, not drawn). HRMO edits reporting lines; each employee sees their head, supervisor and coworkers on My Office. Exports to PNG, PDF and an editable PowerPoint. |
| Salary actions | A daily check finds who is due for a step increment (3 years in the same position) or a loyalty award (10 years, then every 5), with what to verify. HRMO reviews, the Mayor signs the NOSI, and only then does the step change. |
| Reminder lists | Contract renewals (lapsed and ending within 45 days), retirement (past 65, turning 65, optional at 60 with 15 years) and year-end bonus eligibility as of October 31, each exportable to Excel. HRMO and Auditor only. |
| Required meetings | Any meeting the LGU must hold on a schedule (quarterly by default) for everyone, one office or chosen people: each period is scheduled, recorded as held with attendance and where the minutes are filed, or excused; missed periods stand out. On the calendar for the people who attend. |
| Leave (CS Form No. 6) | Employees file online with the working days counted for them and their balance shown before and after. The application goes office head → HRMO (certifies credits) → Mayor, each with an inbox, and is charged to the leave card only when approved. Prints as a filled-in CS Form No. 6 for wet signatures; HRMO can enter paper forms and record paper signatures. Forced leave still to take is shown to each employee. |
| Leave balances | Each person's leave card with running balances; HRMO posts the monthly 1.25 + 1.25 days with one button. The "Leave balances as of" list for the bulletin board prints and exports at any date. |
| Plantilla of Personnel | The posted Plantilla generated from the appointments, by office and item number, with vacant items marked. Print and Excel. HRMO and Auditor only. |
| Vacancies and HRMPSB | Vacancy notices for everyone, with an "I'm interested" button for employees. HRMO tracks applicants and their documents, and prints the request for publication and a draft HRMPSB resolution. |
| Memos | Memos addressed to everyone, an office or chosen people; each employee acknowledges online instead of signing a routing slip, and HRMO and office heads see who has not. Deadlines go on the calendar. |
| Personal Data Sheet | CS Form No. 212 filled in online, section by section, with a progress bar; work in the LGU is filled in from the appointments. HRMO checks it against the 201 file and verifies it or returns it with what to fix; later edits show as changed until sent again. HRMO can encode paper PDS. Prints as the four-page form for signature and oath. Seen only by the person, HRMO and the Auditor. |
| Service records and COEs | The service record (GSIS layout) and certificate of employment, with or without compensation, generated from the appointment history. Employees, including former employees with an account, request online and see when it is ready; HRMO records walk-in, letter and email requests, including from representatives and heirs. Each has a control number and is tracked to release (who received it, ID seen). |
| Roles | HRMO, Office Head, Office Staff, Executive and Delegate, Auditor, Project Maintainer, Employee, System Admin. Each sees only what its role allows; the server checks every request. |
| Audit trail | Every change and every view of a personnel record is logged in a trail that cannot be edited or deleted, with a tamper check the DPO can run independently. |
| Accounts | Bulk employee accounts with one-time passwords that must be changed at first login and expire after 14 days. |
| Quality | 286 automated tests, including forged-request ("Inspect Element") attacks. |

Where things are: the code repository (GitHub, private), `README.md` (setup), `DECISIONS.md` (every rule and why), `docs/DEMO.md` (presentation run sheet).

## 2. Not built yet

| Step | Feature | Waiting on |
|---|---|---|
| 7 | Reports (headcount, tenure, contract expirations, CSC lists) | Which reports HRMO needs first |
| 9 | Excel import of existing records | Sample sheets with personal data blanked out |
| 10 | Employee self-service on phones | HTTPS and an HRMO password-reset screen |

## 3. Before real data can be loaded

This is the Go-Live Checklist in `DECISIONS.md`, in short:

- **Data privacy:** written DPO approval under RA 10173, a Privacy Impact Assessment, and confirmation that the NPC registration covers this system.
- **Real ID numbers:** format checks, then the deliberate removal of the "fake IDs only" rules.
- **Security:** HTTPS, a production web server, a second login step (MFA) for HRMO, Auditor and admin accounts, and a penetration test.
- **Operations:** nightly encrypted backups with a tested restore, the off-server audit anchor kept by the DPO, a server with UPS in a locked room.
- **Network:** decide office-network-only (recommended first) or public internet access.
- **Rollout:** HRMO, Auditor and Office Heads first; employee accounts office by office later.

## 4. Scope

**In scope:** HRMO's personnel records, appointments, documents, projects as they relate to hiring, the HR calendar, the audit trail, and the roadmap steps above.

**Out of scope:** payroll computation (D2), daily time records and attendance (survey finding 6), and systems for other offices.

### Requests from other offices

- **Requests for HRMO's data** (for example Budget wanting payroll inputs, or reports for the Mayor's office): the HRMO Head decides and the DPO reviews anything involving personal data. These are met with reports or narrow roles, such as the Project Maintainer role, never broad access to personnel records.
- **Requests for an office's own system** (Treasurer, Engineering, Assessor, and others): these are new projects. Each needs approval as its own project, a privacy review, and its own budget and maintainer.
- **Every request is written down** (who asked, the problem, the data involved, who should see it) and decided by the HRMO Head, so none are lost and none are built without approval.

## 5. Ownership and maintenance

A live system holding 420 people's personal data needs a named owner who:

- applies security updates within days (Django 5.2 is supported until April 2028);
- checks that backups run and can be restored;
- resets passwords and manages accounts;
- reviews the audit trail with the Auditor and DPO.

**The prototype was built under the developer's current contract. Production operation and work for other offices need to be assigned and resourced by the LGU** (for example through the ICT office, a dedicated position or contract, or procurement) before go-live. The developer can hand over, document and train.

## 6. Decisions needed from the HRMO Head

1. Who owns and maintains the system after the prototype?
2. Office network only, or public internet access?
3. The confirmed leave approval order (built as office head, HRMO, Mayor; one setting changes it).
4. Sample Excel sheets for the import, with personal data blanked out.
4a. Whether former employees keep an account to request service records online, or always go through HRMO (CR6), and whether job order and contract of service stay off the service record (CR1).
5. Who maintains projects: HRMO, Planning, or Budget? (P4)
6. Which reports to build first.
