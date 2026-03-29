# Spacecraft Crew Certification Act 2049

## Synthetic Legal Document — Controlled Test Fixture

> **Purpose:** This document is a synthetic statute designed to exercise every pattern
> supported by the TDA eligibility engine. It serves as a closed-loop regression
> anchor — the legislation never changes, so any engine regression is immediately
> detectable. It is modelled on the structure and language of UK primary legislation
> (cf. British Nationality Act 1981, Immigration Rules Appendix English Language).

---

## Part 1 — General Provisions

### Section 1: Short title and commencement

(1) This Act may be cited as the Spacecraft Crew Certification Act 2049.

(2) This Act comes into force on 1 January 2049.

### Section 2: Interpretation

In this Act—

- "the Authority" means the Galactic Aviation Authority;
- "approved provider" means a body approved under regulations made by the Authority;
- "orbital mission" means a mission involving one or more complete orbits of a celestial body;
- "suborbital mission" means a mission reaching space but not completing a full orbit;
- "lunar mission" means a mission to or around the Moon;
- "eligible species" means any species not excluded under Section 3(2);
- "approved propulsion type" means a propulsion system listed in Section 8(2).

---

## Part 2 — Eligibility Requirements

### Section 3: Species eligibility

*[Pattern: Early termination — disqualifying criterion]*

(1) An applicant for crew certification must be of an eligible species.

(2) A Vogon national is not an eligible species for the purposes of this Act,
    by virtue of the Galactic Diplomatic Exclusion Treaty 2045.

(3) Where the applicant is a Vogon, the application must be refused without
    consideration of any other requirement under this Act.

> **Engine pattern:** `species != "Vogon"`. If species = Vogon, the outcome is
> immediately not eligible — no further questions are asked.

---

### Section 4: Flight readiness

*[Pattern: Multi-field AND — all sub-conditions must be satisfied]*

(1) The applicant must demonstrate flight readiness by satisfying all of
    the following conditions—

    (a) the applicant has accumulated not fewer than 500 flight hours; and

    (b) the applicant holds a valid pilot licence issued or recognised
        by the Authority.

> **Engine pattern:** `And(flight_hours >= 500, has_pilot_license == true)`
> within a single criterion in the `flight_readiness` group.

---

### Section 5: Medical certification

*[Pattern: Multi-route OR — alternative satisfaction paths]*

(1) The applicant must hold a valid medical certificate obtained via one of
    the following routes—

    (a) **Route A:** an examination conducted by the Galactic Aviation Authority; or

    (b) **Route B:** a certification issued by an approved provider.

(2) Satisfaction of either route under subsection (1) is sufficient for the
    purposes of this section.

> **Engine pattern:** Two criteria in the `medical_certification` group.
> Within-group semantics are OR — either route satisfies the group.

---

### Section 5A: Medical certificate validity period

*[Pattern: DATE-bounded requirement — temporal window check]*

(1) A medical certificate obtained under Section 5 is valid for a period
    of 730 days from the date of issue.

(2) The applicant's medical certificate must be valid at the date of
    application; that is, the application date must fall within 730 days
    of the certificate issue date.

(3) For the purposes of subsection (2), dates are expressed in days and
    the certificate is valid where—

    application_date - medical_cert_issue_date <= 730

> **Engine pattern:** `medical_cert_issue_date` (DATE) and `application_date` (DATE),
> both as Int (days since epoch). Validity check:
> `application_date - medical_cert_issue_date <= 730`.
> Since the DSL lacks SUB, this is represented as a pre-computed BOOL field
> `medical_cert_valid` until P5 temporal helpers are implemented.
> In the appendix, this is a BOOL field — the temporal check is done at intake.

---

### Section 6: Age exemption from flight readiness

*[Pattern: Three-level exception chain — "A except B excluding C"]*

(1) Subject to subsection (2), an applicant aged 60 or over is exempt
    from the flight readiness requirement in Section 4.

(2) The exemption under subsection (1) does not apply where the mission
    type is "orbital", unless subsection (3) applies.

(3) An applicant who has accumulated not fewer than 1000 flight hours
    is exempt from Section 4 regardless of mission type, notwithstanding
    subsection (2).

(4) For the avoidance of doubt—

    (a) an applicant aged 60 or over on a suborbital or lunar mission
        is exempt from Section 4 under subsection (1);

    (b) an applicant aged 60 or over on an orbital mission is NOT exempt
        from Section 4, unless the applicant has 1000+ flight hours
        under subsection (3);

    (c) an applicant aged 59 or under must always satisfy Section 4,
        regardless of flight hours.

> **Engine pattern:** Three-level exception chain:
>
> Level 1 (A): `age >= 60` → exempt from flight readiness
> Level 2 (B): `mission_type == "orbital"` → exception: age exemption doesn't apply
> Level 3 (C): `flight_hours >= 1000` → exception to the exception: exempt anyway
>
> Outcome logic for flight readiness becomes:
> ```
> Implies(
>   Not(Or(
>     And(age_exemption, Not(mission_orbital)),     # A except B: age exempt + non-orbital
>     And(age_exemption, veteran_exemption),         # C: age exempt + 1000hrs (any mission)
>     veteran_exemption                              # C standalone: 1000hrs always exempts
>   )),
>   flight_readiness
> )
> ```
>
> Simplified: flight readiness required unless:
> - Age 60+ AND non-orbital mission, OR
> - 1000+ flight hours (regardless of age or mission type)

---

### Section 7: Mission-specific requirements

*[Pattern: Nested IMPLIES — conditional requirement based on mission type]*

(1) Where the mission type is "orbital"—

    (a) the applicant must hold a valid radiation protection certificate.

(2) Where the mission type is "suborbital" or "lunar", no radiation
    protection certificate is required.

> **Engine pattern:** Two groups: `mission_orbital` (mission_type == "orbital")
> and `radiation_clearance` (has_radiation_cert == true).
> Outcome logic: `Implies(mission_orbital, radiation_clearance)`.
> If mission is orbital, radiation cert required. Otherwise, vacuous truth.

---

### Section 8: Equipment compliance

*[Pattern: ENUM field with IN operator — membership check]*

(1) The vessel's propulsion system must be of an approved type.

(2) The approved propulsion types are—

    (a) Infinite Improbability Drive;

    (b) Bistromathics; and

    (c) Heart of Gold Special.

(3) A vessel using Conventional propulsion is not approved for crew
    certification missions.

> **Engine pattern:** `propulsion_type IN ["Infinite Improbability Drive",
> "Bistromathics", "Heart of Gold Special"]`. Compiled to
> `Or(EQ(propulsion_type, v1), EQ(propulsion_type, v2), EQ(propulsion_type, v3))`.

---

### Section 9: Towel compliance

*[Pattern: Simple BOOL — baseline requirement]*

(1) The applicant must carry a towel at all times during the mission, in
    accordance with the Interstellar Hitchhiker Safety Regulations 2042.

> **Engine pattern:** `has_towel == true` in the `towel_compliance` group.

---

## Part 3 — Determination of Outcome

### Section 10: Overall eligibility

(1) An applicant is eligible for crew certification if and only if all of
    the following conditions are met—

    (a) the applicant is of an eligible species (Section 3);

    (b) where the applicant is not exempt under Section 6, the applicant
        satisfies the flight readiness requirements (Section 4);

    (c) the applicant holds a valid medical certificate (Section 5) that
        is within its validity period (Section 5A);

    (d) where the mission type is orbital, the applicant holds a radiation
        protection certificate (Section 7);

    (e) the vessel's propulsion system is of an approved type (Section 8); and

    (f) the applicant carries a towel (Section 9).

> **Outcome logic (AST):**
> ```
> And(
>   species_check,
>   Implies(
>     Not(Or(
>       And(age_exemption, Not(mission_orbital)),    # S.6(1) except S.6(2)
>       veteran_exemption                            # S.6(3) overrides
>     )),
>     flight_readiness
>   ),
>   medical_certification,
>   medical_cert_validity,
>   Implies(mission_orbital, radiation_clearance),
>   equipment_compliance,
>   towel_compliance
> )
> ```

---

## Appendix A — Field Registry

| Key | Sort | Question | Enum Values |
|-----|------|----------|-------------|
| `space.crew.species` | ENUM | What is the applicant's species? | Human, Vogon, Magrathean, Betelgeusian, Dolphin |
| `space.crew.flight_hours` | INT | How many flight hours has the applicant accumulated? | — |
| `space.crew.has_pilot_license` | BOOL | Does the applicant hold a valid pilot licence? | — |
| `space.crew.has_gaa_exam` | BOOL | Has the applicant passed a GAA medical examination? | — |
| `space.crew.has_approved_provider_cert` | BOOL | Does the applicant hold an approved provider medical certificate? | — |
| `space.crew.age` | INT | What is the applicant's age? | — |
| `space.medical.cert_valid` | BOOL | Is the medical certificate within 730 days of application? | — |
| `space.mission.type` | ENUM | What is the mission type? | orbital, suborbital, lunar |
| `space.crew.has_radiation_cert` | BOOL | Does the applicant hold a radiation protection certificate? | — |
| `space.vessel.propulsion_type` | ENUM | What type of propulsion system does the vessel use? | Infinite Improbability Drive, Bistromathics, Conventional, Heart of Gold Special |
| `space.crew.has_towel` | BOOL | Does the applicant carry a towel? | — |

## Appendix B — Groups and Outcome Logic

| Group Name | Criteria (OR within group) | Section |
|------------|---------------------------|---------|
| `species_check` | species != "Vogon" | S.3 |
| `flight_readiness` | And(flight_hours >= 500, has_pilot_license) | S.4 |
| `medical_certification` | has_gaa_exam \| has_approved_provider_cert | S.5 |
| `medical_cert_validity` | medical_cert_valid == true | S.5A |
| `age_exemption` | age >= 60 | S.6(1) |
| `veteran_exemption` | flight_hours >= 1000 | S.6(3) |
| `mission_orbital` | mission_type == "orbital" | S.7 |
| `radiation_clearance` | has_radiation_cert | S.7 |
| `equipment_compliance` | propulsion_type IN [approved list] | S.8 |
| `towel_compliance` | has_towel | S.9 |

## Appendix C — Pattern Coverage Matrix

| # | Pattern | Section | Verified By |
|---|---------|---------|-------------|
| 1 | Multi-field AND | S.4 | `test_flight_readiness_*` |
| 2 | Multi-route OR | S.5 | `test_medical_*` |
| 3 | Three-level exception chain (A except B excluding C) | S.6 | `test_age_exemption_*` |
| 4 | Nested IMPLIES | S.7 | `test_mission_specific_*` |
| 5 | Early termination | S.3 | `test_species_*` |
| 6 | ENUM + IN operator | S.8 | `test_equipment_*` |
| 7 | DATE-bounded validity period | S.5A | `test_medical_cert_validity_*` |
| 8 | Veteran override (exception to exception) | S.6(3) | `test_veteran_exemption_*` |
