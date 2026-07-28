# Verification policy

> Operational policy. Requires CQC compliance lead sign-off, and Dr. Abbass sign-off on any
> public-facing wording derived from it.

## What the badge means
"Credentials checked <date>" asserts **only** that the required documents were checked and remain
in date. It is **not** a competence, quality or outcome claim. Public copy must say so, and
`/how-verification-works` must spell it out.

The displayed date is the **oldest** of the required checks — the badge is only as fresh as its
weakest element.

## Required evidence
| Check | Required for | Expiry |
|---|---|---|
| Photo ID | All | — |
| Regulator / professional body registration | All | Per register renewal |
| Qualification proof | All | — |
| Professional indemnity insurance | All | **Annual — lapses the badge automatically** |
| ICO registration | All | Annual |
| Enhanced DBS | Anyone with an under-18 client group | 3 years, or DBS Update Service |
| Prescriber status | Anyone tagged prescribing | Per register |

## Computed, never toggled
`is_verified`, `credentials_checked_at`, `verification_expires_at` and `minor_work_status` are
written **only** by `directory.services.verification.recompute()`. No admin toggle exists and none
may be added.

Reminders at 60 / 30 / 7 / 0 days before expiry: dashboard banner plus email.

## Under-18 gating
| State | Meaning | Public effect |
|---|---|---|
| `NOT_APPLICABLE` | No under-18 groups selected | — |
| `PROVISIONAL` | DBS pending, window granted | Listing live for **adult work only**. Minor groups hidden from profile and excluded from search. Dashboard shows a countdown. |
| `CLEARED` | Enhanced DBS verified and in date | Minor groups visible and searchable |
| `BLOCKED` | Window elapsed, rejected, or expired | Minor groups locked; admin queue alert |

- Window is **56 days**, granted by a named `verifier`, audit-logged. It does not self-renew.
- Extension is a deliberate act. A **second** extension requires Dr. Abbass sign-off
  (`Practitioner.provisional_extensions` tracks the count).
- On lapse: email the practitioner **and** raise it in the admin queue. Never silent.

## Ongoing monitoring
Registers (GMC, HCPC, BACP, UKCP, BABCP, NMC) are public. **Quarterly re-check** — automated where
a register allows lookup, manual otherwise, recorded on `Registration.last_checked_at`.

A struck-off practitioner sitting live on a Kiam-branded directory is the reputational worst case,
and "we checked once at onboarding" is not a defensible answer.

## Suspension
One-click admin suspend pulls the profile within seconds. Triggers, authorising role and response
time are to be written up and signed off before go-live. Suspension is always audit-logged with a
reason.

## Retention
`Document.delete_after` drives deletion of evidence after a listing is removed. **The period is a
solicitor question** — it balances due-diligence defence against data minimisation. Do not pick a
number in code without that answer; leave it configurable.
