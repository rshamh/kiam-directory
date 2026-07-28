# Content compliance

Human-authored. Propose changes; do not rewrite.

## 1. Prescription-only medicines
UK ASA/CAP rules prohibit advertising prescription-only medicines to the public. **No POM
substance or brand name appears anywhere in public-facing copy** — not in the taxonomy, not in
practitioner bios, not in landing-page intros, not in meta descriptions.

Permitted generic terms: *Medication management*, *Prescribing*, *Titration and review*,
*Shared care*.

Enforcement: the taxonomy contains none by construction; free-text fields (`intro`, `services`,
`availability_note`) are linted at submission against a server-side dictionary
(`settings.POM_DICTIONARY_PATH`). A match **blocks submission** and routes to admin review.

## 2. Efficacy and outcome claims
No "cure", "guaranteed", "proven to", "%" success rates, "miracle", "risk-free", "no side
effects", "best in", "world-leading". Flag list in `directory/taxonomy.py::EFFICACY_CLAIM_FLAGS`.
Flagged copy holds in review rather than auto-publishing.

## 3. Restricted titles
Titles such as *Psychologist*, *Clinical Psychologist*, *Psychiatrist*, *Occupational Therapist*,
*Dietitian*, *Social Worker* are protected or constrained. A practitioner cannot publish under a
`Profession` with `restricted=True` without a **verified** `Registration` from one of its
`required_bodies`. Enforced at review, not just in the UI.

## 4. Child safety
Any client group with `is_minors=True` requires a verified enhanced DBS
(`minor_work_status == CLEARED`) before those groups are publicly visible or searchable.

`PROVISIONAL` means the listing is live for **adult work only**. The gate is applied in two
places — profile serialiser and search queryset — and both are load-bearing.

Residual risk: a provisional practitioner describing child work in free text. Mitigated by
`CHILD_WORK_FLAGS` — those submissions hold in review rather than auto-publishing.

## 5. Independence framing
The notice below appears verbatim on every profile page and every search-results page, and in the
contact-reveal interstitial. Not paraphrased, not abbreviated, not collapsed into a tooltip.

> Practitioners listed in the Kiam Clinic Directory are independent professionals. They are not
> employed by, or part of the clinical team at, Kiam Clinic. Clients arrange appointments and
> payment directly with the practitioner.

## 6. Paid placement
When Featured listings launch, they are **always labelled** as paid placement, visibly, at the
point of display. Undisclosed paid ranking breaches CAP rules.

## 7. Crisis signposting
Persistent, non-alarming, in the footer of every public page:

> If you need urgent help, contact your GP, call 111, or call 999 in an emergency.
> Samaritans: 116 123.

Directory practitioners are not a crisis service and the site says so.

## 8. Clinical content sign-off
Speciality and category descriptions, landing-page editorial intros, verification-policy public
copy and crisis wording are clinical content. **Dr. Abbass signs off before publication.** Mark
unsigned copy `TODO(sign-off)` and keep it out of production templates.

## 9. What would change Kiam's regulatory position
Do not build without prior legal review:
- Taking or holding payment
- Triaging or matching clients to practitioners
- Managing appointments or holding a booking calendar on clients' behalf

Any of these moves Kiam from introducer toward care provider, with CQC consequences.
