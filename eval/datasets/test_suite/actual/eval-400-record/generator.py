"""Generate a 400-record RTM (test_suite_reviewer) dataset for HealthCore EHR.

Seed: eval/datasets/test_suite/actual/pilot-20-record-reviewer/ (the reviewed pilot).
This is a *labels-by-construction* generator: every M1-M5 cell is derived from which
test-case categories are present in the record, and Overall_Verdict is derived from the
mandatory cells exactly as the live SynthesizedAssessment does. That is the property the
reviewed pilot exists to guarantee (labels that agree with content) -- here it holds by
construction rather than by hand review.

Conventions carried over from the reviewed pilot:
  - Overall_Verdict = Yes iff every M1-M5 in {Yes, N-A}. R6 is advisory and omitted.
  - M3 = N-A when the requirement names no threshold/limit/timing edge.
  - M2 = N-A when the requirement exposes no error/validation surface.
  - M1/M4 couple: no positive-path test => functional spec uncovered => M1=No AND M4=No.
  - Terminology drift in the test text (right behaviour, wrong words) => M5=No.
  - M1/M4/M5 are never N-A (validator V031).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path("eval/datasets/test_suite/actual/eval-400-record")
P = "HealthCore EHR"


def tc(tid, desc, setup, steps, exps):
    s = "\n".join(f"Step: {i}. {x}" for i, x in enumerate(steps, 1))
    e = "\n".join(f"ExpectedResult: {i}. {x}" for i, x in enumerate(exps, 1))
    return {"test_id": tid, "description": desc, "setup": setup, "steps": s, "expectedResults": e}


def add_exp(t, text):
    n = t["expectedResults"].count("ExpectedResult:") + 1
    t["expectedResults"] += f"\nExpectedResult: {n}. {text}"


# ----------------------------------------------------------------------------- families
# Each builder(p, rid) -> record dict. A record declares its "ideal" test set plus flags.
# The assembler later injects a single deficiency for known-bad rows.

def f_dose_max(p, rid):
    drug, mx, unit = p
    term = "formulary maximum daily dose"
    a, b, c = f"{rid}-A", f"{rid}-B", f"{rid}-C"
    env = f"{P} test environment; provider logged in; formulary {term} for {drug} is {mx} {unit}."
    pos = tc(a, f"Verify a {drug} order within the {term} is accepted.", env,
             [f"Navigate to Orders -> Medications and select {drug}.",
              f"Enter a regimen totalling {int(mx*0.5)} {unit} per day.", "Click 'Sign & Send'."],
             ["The medication ordering screen is displayed.",
              f"The daily total of {int(mx*0.5)} {unit} is calculated and shown.",
              f"The order is accepted and saved, within the {term} of {mx} {unit}."])
    neg = tc(b, f"Verify a {drug} order exceeding the {term} is rejected.", env,
             [f"Navigate to Orders -> Medications and select {drug}.",
              f"Enter a regimen totalling {int(mx*1.5)} {unit} per day.", "Click 'Sign & Send'."],
             ["The medication ordering screen is displayed.",
              f"The daily total of {int(mx*1.5)} {unit} is calculated and shown.",
              "The order is rejected and is not saved."])
    bnd = tc(c, f"Verify the {term} boundary for {drug}.", env,
             [f"Order {drug} totalling exactly {mx} {unit} per day and sign.",
              f"Order {drug} totalling {mx+1} {unit} per day and sign."],
             [f"At exactly {mx} {unit} the order is accepted, because the dose did not EXCEED the maximum.",
              f"At {mx+1} {unit} the order is rejected."])
    extra = tc(f"{rid}-D", f"Verify the rejection message displays the {term} value.", env,
               [f"Order {drug} totalling {int(mx*1.5)} {unit} per day and attempt to sign."],
               [f"The rejection message states the {term} of {mx} {unit}."])
    req = (f"The system SHALL reject a medication order when the ordered daily dose of {drug} "
           f"exceeds the {term} of {mx} {unit} defined in the formulary, and SHALL display the "
           f"{term} in the rejection message.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=extra, has_neg=True, has_bnd=True,
                multi=True, term=term, wrong="catalog dose ceiling")


def f_lockout(p, rid):
    n, w = p
    term = "lockout notice"
    env = f"{P} test environment; test account 'user.t{n}{w}' active and unlocked; system clock controllable."
    pos = tc(f"{rid}-A", f"Verify the account locks after {n} consecutive failed logins within {w} minutes.", env,
             [f"Submit {n} consecutive failed login attempts for 'user.t{n}{w}' within {w} minutes.",
              "Attempt a further login using the CORRECT password."],
             [f"After the {n}th consecutive failure the account is locked.",
              f"Access is denied despite correct credentials and a {term} naming the account and lockout duration is shown."])
    neg = tc(f"{rid}-B", "Verify a successful login before the threshold resets the counter.", env,
             [f"Submit {n-1} failed attempts, then log in with the correct password.",
              f"Log out and submit {n-1} further failed attempts."],
             [f"After the correct login the failed-attempt counter resets to 0.",
              f"The account remains unlocked and no {term} appears, because no window contains {n} consecutive failures."])
    bnd = tc(f"{rid}-C", f"Verify the {n}-attempt / {w}-minute boundary.", env,
             [f"Submit exactly {n-1} failed attempts within {w} minutes and inspect state.",
              f"Submit an {n}th failed attempt at minute {w-1} and inspect state.",
              f"For a fresh account submit {n-1} failures, wait past minute {w}, then submit one more."],
             [f"At {n-1} failures the account remains unlocked.",
              f"At {n} failures within {w} minutes the account locks and the {term} appears.",
              f"The final failure falls outside the {w}-minute window, so the account remains unlocked."])
    req = (f"The system SHALL lock a user account after {n} consecutive failed login attempts within a "
           f"{w}-minute window and SHALL display a {term} naming the account and the lockout duration.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=None, has_neg=True, has_bnd=True,
                multi=False, term=term, wrong="suspension banner")


def f_session(p, rid):
    m = p
    term = "re-authentication"
    env = f"{P} test environment; provider 'dr.sx{m}' logged in with a patient chart open; session timeout {m} minutes; clock controllable."
    pos = tc(f"{rid}-A", f"Verify a session ends after {m} minutes of no input and requires {term}.", env,
             [f"Open a patient chart and leave the workstation idle for {m} minutes.", "Attempt to scroll the chart."],
             [f"At {m} minutes of inactivity the session ends and the chart is hidden.",
              f"The chart is inaccessible and a {term} prompt is displayed; access is restored only after valid credentials."])
    neg = tc(f"{rid}-B", "Verify input before the timeout keeps the session active.", env,
             [f"Remain idle for {m-1} minutes, then click within the chart.",
              f"Remain idle for a further {m-1} minutes, then click within the chart."],
             ["The session remains active and no prompt appears.",
              "The session remains active because each input resets the inactivity timer."])
    bnd = tc(f"{rid}-C", f"Verify the {m}-minute inactivity boundary.", env,
             [f"Remain idle for exactly {m} minutes minus 1 second; inspect state.",
              f"Allow inactivity to reach exactly {m} minutes 0 seconds; inspect state."],
             [f"Just under {m} minutes the session is still active.",
              f"At exactly {m} minutes the session ends and {term} is required."])
    req = (f"The system SHALL automatically end a clinical session after {m} minutes without user input "
           f"and SHALL require {term} before the chart can be accessed again.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=None, has_neg=True, has_bnd=True,
                multi=False, term=term, wrong="a badge re-tap")


def f_ddi(p, rid):
    sev, da, db = p
    term = "drug-drug interaction alert"
    env = f"{P} test environment; provider logged in; interaction database classifies {da} + {db} as a {sev} interaction."
    pos = tc(f"{rid}-A", f"Verify a {sev} {term} is raised naming both medications.", env,
             [f"Confirm {da} is active for the test patient.",
              f"Order and sign {db} for the patient."],
             [f"{da} is shown as active.",
              f"A {sev} {term} is presented and the alert text names both {da} and {db}."])
    neg = tc(f"{rid}-B", "Verify no alert is raised for a non-interacting pair.", env,
             [f"Confirm the patient has an active medication with no known interaction with the ordered drug.",
              "Order and sign a non-interacting medication."],
             ["The prior medication is active.",
              f"The order is signed with no {term} presented."])
    extra = tc(f"{rid}-C", f"Verify the {term} text names both interacting medications.", env,
               [f"Trigger the {da} + {db} interaction and inspect the alert text."],
               [f"The alert text explicitly contains both '{da}' and '{db}' by name."])
    req = (f"The system SHALL alert the ordering provider when two active medications ({da} and {db}) have a "
           f"known {sev} drug-drug interaction, and the alert SHALL name both interacting medications.")
    return dict(req=req, pos=pos, neg=neg, bnd=None, extra=extra, has_neg=True, has_bnd=False,
                multi=True, term=term, wrong="pharmacy conflict flag")


def f_role(p, rid):
    dc, role = p
    term = dc
    env = f"{P} test environment; user 'auth.t' holds the {role}; a {dc} record exists for the test patient."
    pos = tc(f"{rid}-A", f"Verify a {role} holder can open a {dc} record.", env,
             [f"Log in as a user holding the {role}.", f"Open the patient's {dc}."],
             [f"The {role} is active.", f"The {dc} opens and its content is displayed."])
    neg = tc(f"{rid}-B", f"Verify a user without the {role} is denied access.", env,
             [f"Log in as a user WITHOUT the {role}.", f"Attempt to open the {dc}."],
             [f"Login succeeds without the {role}.", f"Access is denied and no {dc} content is displayed."])
    extra = tc(f"{rid}-C", "Verify each denied attempt is recorded in the audit log.", env,
               [f"As a non-{role} user attempt to open the {dc}.", "As an auditor query the audit log for denials."],
               ["Access is denied.", "An audit entry recording the denied attempt is present."])
    req = (f"The system SHALL restrict access to {dc} to users holding the {role} and SHALL deny access to all "
           f"other roles, recording each denied attempt in the audit log.")
    return dict(req=req, pos=pos, neg=neg, bnd=None, extra=extra, has_neg=True, has_bnd=False,
                multi=True, term=term, wrong=dc.replace("notes", "memos").replace("results", "readouts").replace("records", "files"))


def f_weight(p, rid):
    med, mgkg, dec = p
    term = "recorded body weight"
    raw = 12.345
    env = f"{P} test environment; provider logged in; pediatric patient with a {term}; {med} dosing is {mgkg} mg/kg."
    pos = tc(f"{rid}-A", f"Verify the {med} dose is calculated as {mgkg} mg/kg of {term}.", env,
             [f"Confirm the {term} is 12.0 kg.", f"Select weight-based {med} dosing at {mgkg} mg/kg."],
             [f"The {term} of 12.0 kg is displayed.",
              f"The system calculates and displays a dose of {round(mgkg*12.0, dec)} mg."])
    neg = tc(f"{rid}-B", f"Verify calculation is blocked when no {term} is recorded.", env,
             [f"Open a patient with NO {term}.", f"Select weight-based {med} dosing at {mgkg} mg/kg."],
             [f"The chart shows no {term}.",
              "No dose is calculated; an error states a recorded weight is required and the order cannot be signed."])
    bnd = tc(f"{rid}-C", f"Verify the calculated dose is rounded to {dec} decimal place(s).", env,
             [f"Set the {term} to 12.345 kg and select {mgkg} mg/kg dosing.",
              f"Set the {term} to 12.344 kg and repeat."],
             [f"The displayed dose is {round(mgkg*12.345, dec)} mg, rounded to {dec} decimal place(s).",
              f"The displayed dose shows no more than {dec} decimal place(s)."])
    req = (f"The system SHALL calculate a {med} dose as {mgkg} milligrams per kilogram of the patient's "
           f"{term} and SHALL round the calculated dose to {dec} decimal place(s).")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=None, has_neg=True, has_bnd=True,
                multi=False, term=term, wrong="charted body height")


def f_override(p, rid):
    n, alert = p
    term = "override reason"
    env = f"{P} test environment; provider logged in; test patient triggers a {alert} alert."
    pos = tc(f"{rid}-A", f"Verify a {alert} alert can be dismissed with a sufficient {term}.", env,
             [f"Trigger the {alert} alert and choose to override.",
              f"Enter a {term} of at least {n} characters and confirm."],
             [f"The {alert} alert is presented and a {term} field is shown.",
              f"The override is accepted and the order is signed."])
    neg = tc(f"{rid}-B", f"Verify the alert cannot be dismissed with no {term}.", env,
             [f"Trigger the {alert} alert and choose to override.", f"Leave the {term} empty and confirm."],
             [f"A {term} field is shown.", f"The override is rejected, a message requires a {term}, and the order is not signed."])
    bnd = tc(f"{rid}-C", f"Verify the {n}-character minimum {term} boundary.", env,
             [f"Enter a {term} of {n-1} characters and confirm.",
              f"Enter a {term} of exactly {n} characters and confirm."],
             [f"At {n-1} characters the override is rejected, below the {n}-character minimum.",
              f"At exactly {n} characters the override is accepted."])
    extra = tc(f"{rid}-D", f"Verify the accepted {term} is stored with the order.", env,
               [f"Dismiss the {alert} alert with a valid {term} and open the signed order detail."],
               [f"The stored order detail shows the exact {term} text that was entered."])
    req = (f"The system SHALL require a documented {term} of at least {n} characters when a provider dismisses a "
           f"{alert} alert, and SHALL store the {term} with the order.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=extra, has_neg=True, has_bnd=True,
                multi=True, term=term, wrong="dismissal comment")


def f_mrn(p, rid):
    idf = p
    term = "medical record number"
    env = f"{P} test environment; registration clerk logged in."
    pos = tc(f"{rid}-A", f"Verify a new patient can be registered with a unique {term}.", env,
             [f"Open Patient Registration -> New Patient.", f"Enter demographics with a unique {term} '{idf}-0001'.", "Click 'Register'."],
             ["The registration form is displayed.",
              f"The patient is registered and the chart is created with {term} '{idf}-0001'."])
    neg = tc(f"{rid}-B", f"Verify registration is rejected when the {term} already exists.", env,
             [f"Seed the system with an existing {term} '{idf}-0002'.",
              f"Register a new patient with the same {term} '{idf}-0002'."],
             [f"The existing {term} is present.",
              f"The registration is rejected and no new patient record is created."])
    extra = tc(f"{rid}-C", f"Verify the rejection message displays the conflicting {term}.", env,
               [f"Attempt to register a patient with the duplicate {term} '{idf}-0002'."],
               [f"The rejection message displays the conflicting {term} '{idf}-0002'."])
    req = (f"The system SHALL reject a new patient registration when the supplied {term} ({idf}) already exists in "
           f"the system, and SHALL display the conflicting {term} in the rejection message.")
    return dict(req=req, pos=pos, neg=neg, bnd=None, extra=extra, has_neg=True, has_bnd=False,
                multi=True, term=term, wrong="patient index key")


def f_combo(p, rid):
    va, ta, ua, vb, tb, ub, w = p
    term = "sepsis alert"
    env = f"{P} test environment; test patient admitted; vitals entry available."
    pos = tc(f"{rid}-A", f"Verify a {term} triggers when both {va} and {vb} exceed thresholds within {w} hours.", env,
             [f"Record {va} of {ta+ int(abs(ta)*0.2)+2} {ua} at 09:00.",
              f"Record {vb} of {tb+2} {ub} within the same {w}-hour window."],
             [f"The {va} is recorded.", f"A {term} is triggered and displayed for the patient."])
    neg = tc(f"{rid}-B", f"Verify no {term} triggers when neither value exceeds its threshold.", env,
             [f"Record {va} of {ta-5} {ua}.", f"Record {vb} of {tb-2} {ub}."],
             ["Both values are recorded below threshold.", f"No {term} is triggered."])
    extra = tc(f"{rid}-C", f"Verify no {term} triggers when only one factor exceeds its threshold.", env,
               [f"Record only {va} above {ta} {ua} (with {vb} normal).",
                f"Separately record only {vb} above {tb} {ub} (with {va} normal)."],
               [f"With only {va} elevated, no {term} triggers.",
                f"With only {vb} elevated, no {term} triggers."])
    bnd = tc(f"{rid}-D", f"Verify the exact thresholds and the {w}-hour window boundary.", env,
             [f"Record {va} of exactly {ta} {ua} and {vb} of exactly {tb} {ub}.",
              f"Record both values above threshold but {w} hours and 1 minute apart."],
             [f"At exactly {ta} {ua} / {tb} {ub} no {term} triggers (values must strictly exceed).",
              f"Outside the {w}-hour window no {term} triggers."])
    req = (f"The system SHALL trigger a {term} when a patient's {va} exceeds {ta} {ua} AND the patient's "
           f"{vb} exceeds {tb} {ub} within the same {w}-hour window.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=extra, has_neg=True, has_bnd=True,
                multi=True, term=term, wrong="infection warning")


def f_order(p, rid):
    ot = p
    term = "specimen label"
    env = f"{P} test environment; provider logged in; test patient chart open."
    pos = tc(f"{rid}-A", f"Verify a provider can place a {ot} order with STAT priority.", env,
             [f"Navigate to Orders -> {ot}.", "Select a test and the collection priority 'STAT'.", "Click 'Sign & Send'."],
             [f"The {ot} ordering screen is displayed.",
              "The order is created with priority STAT and appears in the patient's active orders."])
    neg = tc(f"{rid}-B", "Verify the order cannot be signed without a collection priority.", env,
             [f"Navigate to Orders -> {ot} and select a test.", "Leave the collection priority unselected.", "Click 'Sign & Send'."],
             ["A test is selectable.",
              "The order is rejected with a message requiring a priority of STAT or Routine, and no order is created."])
    extra = tc(f"{rid}-C", f"Verify Routine priority and the {term} content.", env,
               ["Place the order with the collection priority 'Routine' and sign.", f"Inspect the printed {term}."],
               ["The order is created with priority Routine.",
                f"The {term} contains the patient's MRN and the {ot} order ID."])
    req = (f"The system SHALL allow a provider to place a {ot} order, SHALL allow selection of a collection priority "
           f"of STAT or Routine, and SHALL print a {term} containing the patient's MRN and the {ot} order ID.")
    return dict(req=req, pos=pos, neg=neg, bnd=None, extra=extra, has_neg=True, has_bnd=False,
                multi=True, term=term, wrong="collection sticker")


def f_banner(p, rid):
    cond, txt = p
    term = f"'{txt}' banner"
    env = f"{P} test environment; clinician logged in."
    pos = tc(f"{rid}-A", f"Verify the {term} appears for a patient with an active {cond} order.", env,
             [f"Open a patient with an active {cond} order.", "Inspect the patient header."],
             ["The patient chart opens.", f"The {term} is displayed on the patient header."])
    neg = tc(f"{rid}-B", f"Verify the {term} is absent when no active {cond} order exists.", env,
             [f"Open a patient with no active {cond} order.", "Inspect the patient header."],
             ["The patient chart opens.", f"The {term} is not displayed."])
    req = (f"The system SHALL display a {term} on the patient header when the patient has an active {cond} order.\n\n"
           f"Context: this requirement applies to active {cond} orders only; non-active {cond} orders are covered elsewhere.")
    return dict(req=req, pos=pos, neg=neg, bnd=None, extra=None, has_neg=True, has_bnd=False,
                multi=False, term=term, wrong="quarantine flag")


def f_mfa(p, rid):
    net = p
    term = "two-factor authentication"
    env = f"{P} test environment; user 'dr.ext' enrolled in {term}; workstation IP outside {net}."
    pos = tc(f"{rid}-A", f"Verify {term} is required and grants access from an external IP.", env,
             [f"From an IP outside {net}, enter valid credentials.", "Enter a valid second factor code."],
             [f"The source IP is recognised as outside {net} and a {term} challenge is presented.",
              "The valid second factor is accepted and access is granted."])
    neg = tc(f"{rid}-B", "Verify an invalid second factor denies access.", env,
             [f"From an IP outside {net}, enter valid credentials.", "Enter an INVALID second factor code."],
             [f"A {term} challenge is presented.", "The invalid second factor is rejected and access is denied."])
    bnd = tc(f"{rid}-C", f"Verify the {net} boundary is enforced at the edge of the range.", env,
             [f"Sign in from the last address inside {net}.", f"Sign in from the first address outside {net}."],
             [f"Inside {net} no {term} is required.",
              f"Just outside {net} a {term} challenge is enforced."])
    req = (f"The system SHALL require {term} for any user signing in from an IP address outside {net}, and SHALL "
           f"grant access only after a valid second factor is supplied.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=None, has_neg=True, has_bnd=True,
                multi=False, term=term, wrong="a secondary passphrase")


def f_archive(p, rid):
    days, secs = p
    term = "cold storage"
    env = f"{P} test environment; archive viewer accessible; clock controllable."
    pos = tc(f"{rid}-A", f"Verify readings older than {days} days are archived to {term} and remain retrievable.", env,
             [f"Locate a vital-sign reading dated {days+30} days ago.", "Inspect its storage tier and open it."],
             [f"The reading is reported as held in {term}.",
              f"Its values are retrieved and displayed within {secs} seconds."])
    neg = tc(f"{rid}-B", f"Verify readings newer than {days} days are not archived to {term}.", env,
             [f"Locate a vital-sign reading dated {max(1, days-60)} days ago.", "Inspect its storage tier."],
             ["The reading is listed.", f"The reading is in active storage and has not been archived to {term}."])
    bnd = tc(f"{rid}-C", f"Verify the {days}-day archival boundary.", env,
             [f"Inspect a reading dated exactly {days} days ago.",
              f"Inspect a reading dated exactly {days+1} days ago."],
             [f"At {days} days the reading is still in active storage.",
              f"At {days+1} days the reading has been archived to {term}."])
    req = (f"The system SHALL archive vital-sign readings older than {days} days to {term} and SHALL keep archived "
           f"readings retrievable within {secs} seconds.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=None, has_neg=True, has_bnd=True,
                multi=False, term=term, wrong="a deep archive vault")


def f_dupadmin(p, rid):
    mins, med = p
    term = "medication administration"
    env = f"{P} test environment; nurse 'nurse.t' logged in; test patient has an active order for {med}."
    pos = tc(f"{rid}-A", f"Verify a nurse can document a {term} of {med}.", env,
             ["Open the Medication Administration Record.", f"Select the active {med} order and document the administration."],
             ["The Medication Administration Record is displayed.",
              f"The {term} is documented and {med} is shown as administered."])
    neg = tc(f"{rid}-B", f"Verify a duplicate {term} within {mins} minutes is blocked.", env,
             [f"Given {med} was administered 5 minutes ago, select the same order.", "Attempt a second administration and confirm."],
             [f"The {med} order is selectable.",
              f"The second administration is blocked citing the duplicate within {mins} minutes, and no second record is written."])
    bnd = tc(f"{rid}-C", f"Verify the {mins}-minute duplicate boundary.", env,
             [f"Attempt a second {term} at exactly {mins} minutes after the first.",
              f"Attempt a second {term} at {mins} minutes minus 1 second after the first."],
             [f"At exactly {mins} minutes the administration is accepted.",
              f"Just under {mins} minutes the administration is blocked."])
    extra = tc(f"{rid}-D", "Verify the administration time is captured and persisted.", env,
               [f"Document a {term} and open the recorded entry."],
               ["The recorded entry stores the administration time."])
    req = (f"The system SHALL let a nurse document a {term} of {med}, SHALL capture the administration time, and "
           f"SHALL block a duplicate {term} within {mins} minutes of the previous one for the same medication.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=extra, has_neg=True, has_bnd=True,
                multi=True, term=term, wrong="medication handoff")


def f_critical(p, rid):
    mins, analyte, channel = p
    term = "critical lab value notification"
    env = f"{P} test environment; provider 'dr.t' has an outstanding {analyte} order; {channel} accessible."
    pos = tc(f"{rid}-A", f"Verify a {term} for {analyte} is delivered within {mins} minutes of verification.", env,
             [f"Verify a critical {analyte} result and record the verification time.",
              f"Open the {channel} and record the notification arrival time."],
             [f"The critical {analyte} result is verified.",
              f"A {term} is present in the {channel} and the interval is at or under {mins} minutes."])
    neg = tc(f"{rid}-B", "Verify duplicate verification events do not create a second notification.", env,
             [f"Emit the same verified {analyte} result twice.", f"Inspect the {channel}."],
             ["The result is verified twice.",
              f"Exactly one {term} is present in the {channel}; the duplicate is suppressed."])
    bnd = tc(f"{rid}-C", f"Verify the {mins}-minute delivery boundary.", env,
             [f"Verify a critical {analyte} result and measure delivery latency to the {channel}."],
             [f"The {term} is recorded as delivered within {mins} minutes of verification."])
    extra = tc(f"{rid}-D", "Verify the notification routes to the ordering provider specifically.", env,
               [f"Verify a critical {analyte} result ordered by 'dr.t' and inspect the recipient."],
               [f"The {term} is routed to the ordering provider's {channel}, not to any other recipient."])
    req = (f"The system SHALL deliver a {term} for {analyte} to the ordering provider's {channel} within {mins} "
           f"minutes of result verification.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=extra, has_neg=True, has_bnd=True,
                multi=True, term=term, wrong="abnormal result alert")


def f_rx(p, rid):
    secs = p
    term = "signed prescription"
    env = f"{P} test environment; provider logged in; test pharmacy 'Riverside #2' configured to confirm receipt."
    pos = tc(f"{rid}-A", f"Verify a {term} is transmitted to the selected pharmacy.", env,
             ["Select a medication and destination pharmacy.", "Click 'Sign & Send' and observe transmission."],
             ["The pharmacy is selected.", f"The {term} is transmitted to the selected pharmacy."])
    neg = tc(f"{rid}-B", "Verify status does not become 'Sent' when the pharmacy is unreachable.", env,
             ["Disable the pharmacy endpoint.", "Sign and send a prescription and observe status."],
             ["The endpoint is unreachable.", "Transmission fails, an error is shown, and the status does NOT become 'Sent'."])
    bnd = tc(f"{rid}-C", f"Verify transmission occurs within the {secs}-second limit.", env,
             [f"Sign a {term} and record the signing time.", "Record the arrival time at the pharmacy interface."],
             [f"The elapsed interval between signing and arrival is at or under {secs} seconds."])
    extra = tc(f"{rid}-D", "Verify the order status updates to 'Sent' upon pharmacy confirmation.", env,
               ["Transmit a prescription and have the pharmacy return confirmation."],
               ["On pharmacy confirmation the order status updates to 'Sent'."])
    req = (f"The system SHALL transmit a {term} to the selected pharmacy within {secs} seconds of signing and SHALL "
           f"update the order status to 'Sent' upon receiving pharmacy confirmation.")
    return dict(req=req, pos=pos, neg=neg, bnd=bnd, extra=extra, has_neg=True, has_bnd=True,
                multi=True, term=term, wrong="approved script")


def f_vitals(p, rid):
    calc, inputs, dec = p
    term = calc
    env = f"{P} test environment; clinician logged in; test patient has recorded {inputs}."
    pos = tc(f"{rid}-A", f"Verify {term} is computed from {inputs} and displayed.", env,
             [f"Open a patient with recorded {inputs}.", "Inspect the patient header."],
             [f"The {inputs} are shown.", f"The {term} is computed from {inputs} and displayed on the patient header."])
    bnd = tc(f"{rid}-B", f"Verify the displayed {term} is rounded to {dec} decimal place(s).", env,
             [f"Set {inputs} to values whose {term} is non-terminating and inspect the header."],
             [f"The displayed {term} shows no more than {dec} decimal place(s)."])
    req = (f"The system SHALL display the {term} computed from {inputs} on the patient header, rounded to {dec} "
           f"decimal place(s).\n\nContext: handling of missing or invalid {inputs} is specified elsewhere.")
    return dict(req=req, pos=pos, neg=None, bnd=bnd, extra=None, has_neg=False, has_bnd=True,
                multi=False, term=term, wrong="pulse pressure index")


# ----------------------------------------------------------------------------- param banks
DRUGS = [("Acetaminophen", 4000, "mg"), ("Ibuprofen", 3200, "mg"), ("Metformin", 2550, "mg"),
         ("Gabapentin", 3600, "mg"), ("Sertraline", 200, "mg"), ("Amlodipine", 10, "mg"),
         ("Lisinopril", 80, "mg"), ("Furosemide", 600, "mg"), ("Levothyroxine", 300, "mcg"),
         ("Warfarin", 10, "mg"), ("Morphine", 120, "mg"), ("Oxycodone", 80, "mg"),
         ("Prednisone", 60, "mg"), ("Amoxicillin", 4000, "mg"), ("Ciprofloxacin", 1500, "mg"),
         ("Azithromycin", 500, "mg"), ("Hydrochlorothiazide", 50, "mg"), ("Atorvastatin", 80, "mg"),
         ("Omeprazole", 40, "mg"), ("Tramadol", 400, "mg"), ("Diazepam", 40, "mg"),
         ("Vancomycin", 4000, "mg"), ("Enoxaparin", 180, "mg"), ("Clopidogrel", 75, "mg"),
         ("Metoprolol", 400, "mg"), ("Spironolactone", 400, "mg"), ("Fluoxetine", 80, "mg"),
         ("Cephalexin", 4000, "mg"), ("Naproxen", 1500, "mg"), ("Duloxetine", 120, "mg"),
         ("Losartan", 100, "mg"), ("Pantoprazole", 240, "mg"), ("Citalopram", 40, "mg"),
         ("Venlafaxine", 375, "mg"), ("Bupropion", 450, "mg"), ("Quetiapine", 800, "mg"),
         ("Carvedilol", 50, "mg"), ("Allopurinol", 800, "mg"), ("Hydralazine", 300, "mg"),
         ("Torsemide", 200, "mg")]

DDI = [("major", "Warfarin", "Aspirin"), ("major", "Warfarin", "Fluconazole"),
       ("major", "Clopidogrel", "Omeprazole"), ("major", "Simvastatin", "Clarithromycin"),
       ("major", "Digoxin", "Amiodarone"), ("major", "Methotrexate", "Trimethoprim"),
       ("major", "Lithium", "Ibuprofen"), ("major", "Sildenafil", "Nitroglycerin"),
       ("major", "Fluoxetine", "Tramadol"), ("major", "Verapamil", "Metoprolol"),
       ("major", "Spironolactone", "Potassium chloride"), ("major", "Warfarin", "Amiodarone"),
       ("major", "Linezolid", "Sertraline"), ("major", "Allopurinol", "Azathioprine"),
       ("major", "Ciprofloxacin", "Tizanidine"), ("major", "Rifampin", "Warfarin"),
       ("moderate", "Metformin", "Contrast dye"), ("moderate", "Atorvastatin", "Diltiazem"),
       ("moderate", "Levothyroxine", "Calcium carbonate"), ("moderate", "Amlodipine", "Simvastatin"),
       ("moderate", "Ciprofloxacin", "Sucralfate"), ("moderate", "Prednisone", "Ibuprofen"),
       ("moderate", "Lisinopril", "Ibuprofen"), ("moderate", "Carbamazepine", "Doxycycline"),
       ("major", "Warfarin", "Metronidazole"), ("major", "Digoxin", "Verapamil"),
       ("major", "Clarithromycin", "Colchicine"), ("major", "Phenytoin", "Fluconazole"),
       ("moderate", "Furosemide", "Gentamicin"), ("moderate", "Sertraline", "Ibuprofen")]

ROLES = [("behavioral-health notes", "Behavioral Health role"), ("HIV test results", "Infectious Disease role"),
         ("genetic test results", "Genetic Counseling role"), ("substance-use treatment notes", "Addiction Medicine role"),
         ("psychotherapy notes", "Psychiatry role"), ("sexual-health records", "Adolescent Medicine role"),
         ("VIP patient records", "Privacy Officer role"), ("employee-patient charts", "Occupational Health role"),
         ("adoption records", "Social Work role"), ("minor confidential records", "Pediatric Confidential role"),
         ("sexual-assault examination records", "Forensic Nursing role"), ("gender-affirming care notes", "Endocrinology role"),
         ("research-only study data", "Clinical Research role"), ("legal-hold chart annotations", "Compliance role"),
         ("donor-recipient matching records", "Transplant role"), ("military-service health records", "Veterans Care role"),
         ("court-ordered evaluation notes", "Forensic Psychiatry role"), ("fertility treatment records", "Reproductive Endocrinology role"),
         ("child-protection screening notes", "Child Protection role"), ("workers-compensation records", "Case Management role"),
         ("prison-inmate health records", "Correctional Health role"), ("immunization-exemption records", "Public Health role"),
         ("palliative-care directives", "Palliative Care role"), ("newborn screening results", "Neonatology role"),
         ("occupational-exposure records", "Employee Health role"), ("celebrity-alias charts", "Executive Privacy role"),
         ("hospice eligibility notes", "Hospice role"), ("mental-health crisis notes", "Crisis Intervention role"),
         ("sealed-record annotations", "Health Information Management role"), ("high-risk obstetric notes", "Maternal-Fetal Medicine role")]

WEIGHT = [("Amoxicillin", 10, 1), ("Gentamicin", 2, 2), ("Vancomycin", 15, 1), ("Acetaminophen", 15, 1),
          ("Ibuprofen", 10, 1), ("Cefazolin", 25, 1), ("Ondansetron", 0.15, 2), ("Furosemide", 1, 1),
          ("Ceftriaxone", 50, 1), ("Clindamycin", 8, 1), ("Ampicillin", 25, 1), ("Dexamethasone", 0.15, 2),
          ("Morphine", 0.1, 2), ("Midazolam", 0.1, 2), ("Prednisolone", 1, 1), ("Ranitidine", 2, 1),
          ("Metronidazole", 7, 1), ("Azithromycin", 10, 1), ("Fentanyl", 0.001, 3), ("Ketamine", 1, 1),
          ("Diphenhydramine", 1, 1), ("Albuterol", 0.15, 2), ("Enoxaparin", 1, 1), ("Fluconazole", 6, 1),
          ("Lorazepam", 0.05, 2), ("Phenobarbital", 5, 1)]

OVERRIDE = [(n, a) for n in (8, 10, 12, 15, 20, 25) for a in
            ("high-severity allergy", "hard-stop dosing", "critical drug-interaction", "duplicate-therapy")][:24]

MRN = [f"MRN-{s}" for s in ("A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P",
                            "Q", "R", "S", "T", "U", "W")]

COMBO = [("heart rate", 90, "bpm", "temperature", 38, "degrees Celsius", 4),
         ("respiratory rate", 22, "breaths/min", "temperature", 38, "degrees Celsius", 6),
         ("systolic blood pressure", 180, "mmHg", "heart rate", 110, "bpm", 2),
         ("white blood cell count", 12, "10^9/L", "temperature", 38, "degrees Celsius", 24),
         ("lactate", 2, "mmol/L", "heart rate", 100, "bpm", 6),
         ("heart rate", 100, "bpm", "respiratory rate", 20, "breaths/min", 1),
         ("temperature", 39, "degrees Celsius", "heart rate", 120, "bpm", 3),
         ("systolic blood pressure", 90, "mmHg", "respiratory rate", 24, "breaths/min", 4),
         ("creatinine", 2, "mg/dL", "potassium", 5, "mmol/L", 12),
         ("heart rate", 130, "bpm", "systolic blood pressure", 90, "mmHg", 2),
         ("bilirubin", 3, "mg/dL", "temperature", 38, "degrees Celsius", 8),
         ("glucose", 250, "mg/dL", "heart rate", 100, "bpm", 6),
         ("respiratory rate", 24, "breaths/min", "oxygen saturation drop below", 92, "percent", 1),
         ("INR", 4, "ratio", "systolic blood pressure", 90, "mmHg", 6),
         ("temperature", 38, "degrees Celsius", "systolic blood pressure", 90, "mmHg", 4),
         ("heart rate", 90, "bpm", "white blood cell count", 12, "10^9/L", 6),
         ("lactate", 4, "mmol/L", "systolic blood pressure", 90, "mmHg", 3),
         ("heart rate", 90, "bpm", "respiratory rate", 20, "breaths/min", 4),
         ("temperature", 38, "degrees Celsius", "heart rate", 90, "bpm", 8),
         ("potassium", 6, "mmol/L", "heart rate", 120, "bpm", 2)]

ORDER = ["laboratory", "radiology", "microbiology", "pathology", "blood-bank", "genetics", "cardiology-diagnostic",
         "pulmonary-function", "electrophysiology", "cytology", "toxicology", "molecular-diagnostics",
         "point-of-care testing", "sleep-study", "nuclear-medicine", "interventional-radiology", "endoscopy",
         "biopsy", "coagulation", "serology"]

BANNER = [("contact-isolation", "Isolation Precautions"), ("fall-risk", "Fall Risk"), ("do-not-resuscitate", "DNR"),
          ("latex-allergy", "Latex Allergy"), ("elopement-risk", "Elopement Risk"), ("aspiration-precaution", "Aspiration Precautions"),
          ("seizure-precaution", "Seizure Precautions"), ("suicide-watch", "Safety Watch"), ("airborne-isolation", "Airborne Precautions"),
          ("droplet-isolation", "Droplet Precautions"), ("neutropenic-precaution", "Neutropenic Precautions"),
          ("restraint-order", "Restraint In Use"), ("bariatric-equipment", "Bariatric Precautions"), ("VIP-privacy", "Privacy Restricted"),
          ("organ-donor", "Organ Donor"), ("code-status-limited", "Limited Code"), ("interpreter-required", "Interpreter Required"),
          ("wandering-risk", "Wandering Risk"), ("MRI-unsafe-implant", "MRI Unsafe"), ("high-bleeding-risk", "Bleeding Precautions")]

NETWORK = ["the hospital network range", "the campus 10.0.0.0/8 range", "the clinical VLAN range",
           "the on-premises 172.16.0.0/12 range", "the corporate 192.168.0.0/16 range", "the trusted facility subnet",
           "the internal enterprise range", "the primary data-center range", "the branch-clinic VPN range",
           "the telehealth gateway range", "the pharmacy network segment", "the emergency-department subnet",
           "the imaging-suite VLAN", "the administrative office range", "the research-network segment", "the lab-information subnet",
           "the surgical-suite subnet", "the outpatient-clinic range", "the radiology reading-room VLAN", "the biomedical-device segment"]

ARCHIVE = [(d, s) for d in (30, 45, 60, 90, 120, 180, 270, 365) for s in (2, 5, 10)][:24]

DUPADMIN = [(m, med) for m in (15, 30, 45, 60) for med in
            ("Ondansetron 4 mg", "Morphine 2 mg", "Insulin lispro 4 units", "Furosemide 40 mg",
             "Acetaminophen 650 mg", "Heparin 5000 units")][:24]

CRITICAL = [(m, a, "secure inbox") for m in (5, 10, 15) for a in
            ("potassium", "troponin", "hemoglobin", "INR", "glucose", "lactate", "sodium", "calcium")][:24]

RXSECS = [30, 45, 60, 90, 120, 15, 20, 180, 300, 240, 75, 50, 10, 25, 40, 150, 200, 80, 110, 65, 95, 55]

LOCKOUT = [(n, w) for n in (3, 4, 5, 6, 7) for w in (10, 15, 20, 30)]

SESSION = [5, 10, 15, 20, 25, 30, 45, 60, 8, 12, 18, 90, 120, 40, 3, 7, 35, 50, 22, 28]

VITALS = [("mean arterial pressure", "systolic and diastolic blood pressure", 0),
          ("body mass index", "height and weight", 1), ("pulse pressure", "systolic and diastolic blood pressure", 0),
          ("estimated GFR", "creatinine, age and sex", 0), ("corrected QT interval", "QT interval and heart rate", 0),
          ("body surface area", "height and weight", 2), ("anion gap", "sodium, chloride and bicarbonate", 0),
          ("ideal body weight", "height and sex", 1), ("corrected calcium", "calcium and albumin", 1),
          ("Glasgow Coma Score", "eye, verbal and motor responses", 0), ("shock index", "heart rate and systolic pressure", 2),
          ("MELD score", "bilirubin, INR and creatinine", 0), ("APGAR score", "five newborn criteria", 0),
          ("estimated blood volume", "weight and age", 0), ("mean corpuscular volume", "hematocrit and red cell count", 1),
          ("fractional excretion of sodium", "urine and serum sodium and creatinine", 2)]

FAMILIES = [
    ("dose_max", f_dose_max, DRUGS),
    ("lockout", f_lockout, LOCKOUT),
    ("session", f_session, SESSION),
    ("ddi", f_ddi, DDI),
    ("role", f_role, ROLES),
    ("weight", f_weight, WEIGHT),
    ("override", f_override, OVERRIDE),
    ("mrn", f_mrn, MRN),
    ("combo", f_combo, COMBO),
    ("order", f_order, ORDER),
    ("banner", f_banner, BANNER),
    ("mfa", f_mfa, NETWORK),
    ("archive", f_archive, ARCHIVE),
    ("dupadmin", f_dupadmin, DUPADMIN),
    ("critical", f_critical, CRITICAL),
    ("rx", f_rx, RXSECS),
    ("vitals", f_vitals, VITALS),
]

# ----------------------------------------------------------------------------- assemble
def slots():
    """Round-robin interleave (family, param) across families for ordering variety."""
    pools = [[(name, fn, p) for p in params] for name, fn, params in FAMILIES]
    out, i = [], 0
    while any(pools):
        pool = pools[i % len(pools)]
        if pool:
            out.append(pool.pop(0))
        i += 1
    return out


def drift(tests, term, wrong):
    out = []
    for t in tests:
        t2 = dict(t)
        for k in ("description", "setup", "steps", "expectedResults"):
            t2[k] = t2[k].replace(term, wrong)
        out.append(t2)
    return out


def assemble(rec, mode):
    # ensure the terminology phrase is present so an M5 drift is always visible
    if rec["term"] not in rec["pos"]["expectedResults"]:
        add_exp(rec["pos"], f"The {rec['term']} wording matches the requirement exactly.")
    tests = [rec["pos"]]
    if rec["neg"]:
        tests.append(rec["neg"])
    if rec["bnd"]:
        tests.append(rec["bnd"])
    if rec["extra"]:
        tests.append(rec["extra"])
    cells = {"M1": "Yes", "M2": "Yes" if rec["has_neg"] else "N-A",
             "M3": "Yes" if rec["has_bnd"] else "N-A", "M4": "Yes", "M5": "Yes"}
    pf = None
    if mode == "M1":
        tests = [t for t in tests if t is not rec["pos"]]
        cells["M1"], cells["M4"], pf = "No", "No", "M1"
    elif mode == "M2":
        tests = [t for t in tests if t is not rec["neg"]]
        cells["M2"], pf = "No", "M2"
    elif mode == "M3":
        tests = [t for t in tests if t is not rec["bnd"]]
        cells["M3"], pf = "No", "M3"
    elif mode == "M4":
        tests = [t for t in tests if t is not rec["extra"]]
        cells["M4"], pf = "No", "M4"
    elif mode == "M5":
        tests = drift(tests, rec["term"], rec["wrong"])
        cells["M5"], pf = "No", "M5"
    overall = "Yes" if all(cells[m] in ("Yes", "N-A") for m in ("M1", "M2", "M3", "M4", "M5")) else "No"
    return tests, cells, overall, pf


def main():
    sl = slots()[:400]
    # build records first (needs rid)
    built = []
    for k, (name, fn, p) in enumerate(sl):
        rid = f"REQ-HC-{100 + k}"
        rec = fn(p, f"TC-HC-{100 + k}")
        rec["req_id"] = rid
        rec["family"] = name
        built.append(rec)

    # --- mode allocation: 200 good / 200 bad, 40 per failing cell, respecting capability
    rng = random.Random(42)
    idx = list(range(len(built)))
    rng.shuffle(idx)
    avail = set(idx)
    modes = {}

    def grab(n, cond):
        chosen = [i for i in idx if i in avail and cond(i)][:n]
        for i in chosen:
            avail.discard(i)
            modes[i] = None
        return chosen

    for i in grab(40, lambda i: built[i]["has_bnd"]):
        modes[i] = "M3"
    for i in grab(40, lambda i: built[i]["multi"]):
        modes[i] = "M4"
    for i in grab(40, lambda i: built[i]["has_neg"]):
        modes[i] = "M2"
    for i in grab(40, lambda i: True):
        modes[i] = "M1"
    for i in grab(40, lambda i: True):
        modes[i] = "M5"
    for i in range(len(built)):
        modes.setdefault(i, "good")

    inputs, labels = [], []
    seen = set()
    dist = {}
    for i, rec in enumerate(built):
        assert rec["req"] not in seen, f"duplicate requirement text at {i}"
        seen.add(rec["req"])
        tests, cells, overall, pf = assemble(rec, modes[i])
        # V031 guard
        assert cells["M1"] != "N-A" and cells["M4"] != "N-A" and cells["M5"] != "N-A"
        # V040 guard
        derived = "Yes" if all(cells[m] in ("Yes", "N-A") for m in ("M1", "M2", "M3", "M4", "M5")) else "No"
        assert overall == derived
        inputs.append({"requirement": {"req_id": rec["req_id"], "text": rec["req"]},
                       "test_cases": tests})
        row = {"Overall_Verdict": overall, **cells,
               "class": "known_good" if overall == "Yes" else "known_bad"}
        if pf:
            row["primary_failure"] = pf
        labels.append(row)
        key = "good" if modes[i] == "good" else pf
        dist[key] = dist.get(key, 0) + 1

    OUT.joinpath("actual_inputs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in inputs) + "\n", encoding="utf-8")
    OUT.joinpath("actual_labels.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in labels) + "\n", encoding="utf-8")

    goods = sum(1 for r in labels if r["Overall_Verdict"] == "Yes")
    print(f"wrote {len(inputs)} rows -> {OUT}")
    print(f"class balance: {goods} good / {len(labels) - goods} bad")
    print(f"failure distribution: {dict(sorted(dist.items()))}")
    fam = {}
    for r in built:
        fam[r["family"]] = fam.get(r["family"], 0) + 1
    print(f"family coverage: {dict(sorted(fam.items()))}")


if __name__ == "__main__":
    main()
