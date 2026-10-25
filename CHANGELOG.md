# Changelog

The version in every page footer is `MAJOR.MINOR.PATCH` from the `VERSION` file (DECISIONS H9):

- **PATCH** (1.1.**1**): a release with bug fixes only.
- **MINOR** (1.**2**.0): a release that adds features. The patch number goes back to 0.
- **MAJOR** (**2**.0.0): a change that breaks how people work or what is stored, such as going live with real data.

Bump `VERSION`, add a section here, and tag the commit (`git tag v1.2.0`) in the same release. Hover over the
version in the footer to see the exact commit that is running.

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
