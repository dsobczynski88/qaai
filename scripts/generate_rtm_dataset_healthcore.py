#!/usr/bin/env python3
"""
Generate 800-record labelled RTM dataset for HealthCore EHR Suite.

Domain: Medical Device Software (EHR/Healthcare IT, IEC 82304 / HIPAA / FDA 21 CFR Part 11)
Product: HealthCore EHR Suite — Class II SaMD for electronic health records
Output: tests/fixtures/generated/{inputs,outputs}.jsonl + description.md

Execution: 16 batches × 50 records (25 known-good, 25 known-bad per batch)
Subsystem distribution: Vitals (35%), CDS (35%), ePrescribing (15%), AccessControl (10%), DataMgmt (5%)
M2/M3 N-A frequency: M2 5%, M3 20% (known-goods only)

Usage:
    python scripts/generate_rtm_dataset_healthcore.py --batch 1
"""

from __future__ import annotations
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from autoqa.components.test_suite_reviewer.core import (
    Requirement,
    TestCase,
    DecomposedSpec,
    DecomposedRequirement,
    SummarizedTestCase,
    TestSuite,
    CoveringTestCase,
    EvaluatedSpec,
    MandatoryFinding,
    SynthesizedAssessment,
)


# Configuration
TOTAL_RECORDS = 800
TOTAL_BATCHES = 16
RECORDS_PER_BATCH = 50
KNOWN_GOOD_PER_BATCH = 25
KNOWN_BAD_PER_BATCH = 25

# Subsystem distribution (prioritizing Vitals & CDS)
SUBSYSTEMS = {
    "Vitals": {"weight": 0.35, "count": 280, "range": (100, 379), "prefix": "VIT"},
    "CDS": {"weight": 0.35, "count": 280, "range": (380, 659), "prefix": "CDS"},
    "ePrescribing": {"weight": 0.15, "count": 120, "range": (660, 779), "prefix": "EPR"},
    "AccessControl": {"weight": 0.10, "count": 80, "range": (780, 859), "prefix": "ACC"},
    "DataManagement": {"weight": 0.05, "count": 40, "range": (860, 899), "prefix": "DAT"},
}

# M2/M3 N-A frequency for known-goods
M2_NA_FREQUENCY = 0.05  # 5%
M3_NA_FREQUENCY = 0.20  # 20%

# Failure dimensions
FAILURE_DIMS = ["M1", "M2", "M3", "M4", "M5"]

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "generated"


def get_subsystem_for_record(record_idx: int) -> str:
    """Determine subsystem based on record index."""
    if 100 <= record_idx <= 379:
        return "Vitals"
    elif 380 <= record_idx <= 659:
        return "CDS"
    elif 660 <= record_idx <= 779:
        return "ePrescribing"
    elif 780 <= record_idx <= 859:
        return "AccessControl"
    elif 860 <= record_idx <= 899:
        return "DataManagement"
    else:
        raise ValueError(f"Record index {record_idx} out of range")


def generate_requirement_text(subsystem: str, req_id: str, record_idx: int) -> str:
    """Generate realistic requirement text based on subsystem."""
    
    templates = {
        "Vitals": [
            f"The system SHALL automatically import heart rate, blood pressure, and oxygen saturation (SpO2) from connected continuous vitals monitors every {5 + (record_idx % 3)} minutes.",
            f"The system SHALL alert clinical staff when heart rate exceeds {120 + (record_idx % 30)} bpm for more than {30 + (record_idx % 60)} seconds.",
            f"The system SHALL display real-time vital signs trends on the patient monitoring dashboard with updates every {10 + (record_idx % 10)} seconds.",
            f"The vitals monitoring module SHALL validate incoming sensor data against physiological ranges (HR: 30-250 bpm, BP: 40-250 mmHg, SpO2: 70-100%) before display.",
            f"The system SHALL log all vitals data transmission failures with timestamp, device ID, and error code for audit purposes.",
        ],
        "CDS": [
            f"The clinical decision support module SHALL generate a high-priority alert when patient data indicates potential drug-drug interaction with severity level {record_idx % 3 + 1}.",
            f"The CDS engine SHALL recommend alternative medications when the prescribed drug is contraindicated based on patient allergy history.",
            f"The system SHALL cross-reference laboratory results against clinical guidelines and flag values outside normal ranges within {30 + (record_idx % 30)} seconds.",
            f"The CDS module SHALL display evidence-based treatment recommendations when a new diagnosis is entered, prioritized by clinical relevance score.",
            f"The system SHALL suppress duplicate CDS alerts for the same condition within a {24 + (record_idx % 48)}-hour window to reduce alert fatigue.",
        ],
        "ePrescribing": [
            f"The e-prescribing module SHALL validate medication dosage against FDA-approved maximums before allowing prescription submission.",
            f"The system SHALL require provider justification when prescribing a controlled substance (Schedule II-IV) for more than {30 + (record_idx % 60)} days.",
            f"The prescription interface SHALL allow providers to select the patient's preferred pharmacy from a nationwide directory with real-time availability status.",
            f"The e-prescribing module SHALL automatically check for drug-allergy interactions and block submission if a severe allergy match is detected.",
            f"The system SHALL transmit electronic prescriptions to pharmacies using NCPDP SCRIPT standard within {60 + (record_idx % 60)} seconds of provider signature.",
        ],
        "AccessControl": [
            f"The system SHALL restrict access to mental health session notes to the assigned provider unless the 'Emergency Break-Glass' protocol is initiated with audit reason.",
            f"The system SHALL automatically invalidate session tokens and log out users after {15 + (record_idx % 15)} minutes of inactivity to comply with HIPAA regulations.",
            f"The access control module SHALL log all failed login attempts with username, timestamp, IP address, and reason code for security audit.",
            f"The system SHALL enforce role-based access control (RBAC) for patient data, restricting viewing permissions based on user role (Physician, Nurse, Admin).",
            f"The system SHALL require multi-factor authentication (MFA) for users accessing patient health information (PHI) from external networks.",
        ],
        "DataManagement": [
            f"The system SHALL export patient data in HL7 FHIR R4 format compliant with US Core Implementation Guide within {120 + (record_idx % 120)} seconds of request.",
            f"The data management module SHALL synchronize patient demographics with the Master Patient Index (MPI) every {60 + (record_idx % 60)} minutes.",
            f"The system SHALL maintain referential integrity for patient-encounter relationships through cascading delete constraints.",
            f"The data management module SHALL archive patient records older than {7 + (record_idx % 3)} years to cold storage while maintaining query access.",
            f"The system SHALL validate incoming HL7 v2.x messages against schema and reject malformed messages with descriptive error codes.",
        ],
    }
    
    template_list = templates.get(subsystem, templates["Vitals"])
    return template_list[record_idx % len(template_list)]


def generate_decomposed_specs(req_id: str, req_text: str, num_specs: int, subsystem: str) -> List[Dict[str, str]]:
    """Generate decomposed specifications for a requirement."""
    specs = []
    
    # Extract key phrases from requirement for realistic decomposition
    if "SHALL" in req_text:
        main_action = req_text.split("SHALL")[1].split(".")[0].strip()
    else:
        main_action = req_text[:100]
    
    for i in range(num_specs):
        spec_id = f"{req_id}-{i+1:02d}"
        
        if i == 0:
            description = f"Core functional behavior: {main_action[:80]}"
            acceptance_criteria = f"System successfully executes the primary action specified in {req_id}"
        elif i == 1:
            description = f"Error handling and validation for {req_id}"
            acceptance_criteria = f"System rejects invalid inputs and displays appropriate error messages"
        elif i == 2:
            description = f"Timing and performance constraints for {req_id}"
            acceptance_criteria = f"System meets specified timing requirements within acceptable tolerance"
        elif i == 3:
            description = f"Audit logging and traceability for {req_id}"
            acceptance_criteria = f"All actions are logged with timestamp, user ID, and outcome"
        else:
            description = f"Additional specification {i+1} for {req_id}"
            acceptance_criteria = f"Supplementary acceptance criteria for spec {spec_id}"
        
        specs.append({
            "spec_id": spec_id,
            "description": description,
            "acceptance_criteria": acceptance_criteria,
            "rationale": f"Decomposed from {req_id} to enable atomic verification of {subsystem} functionality"
        })
    
    return specs


def generate_test_cases_known_good(
    req_id: str,
    specs: List[Dict[str, str]],
    subsystem: str,
    use_m2_na: bool,
    use_m3_na: bool,
    record_idx: int
) -> List[Dict[str, Any]]:
    """Generate test cases for known-good record with full coverage."""
    test_cases = []
    tc_num = req_id.split("-")[-1]
    
    # Functional TC (always present)
    test_cases.append({
        "test_id": f"TC-HC-{tc_num}-A",
        "description": f"Verify functional behavior for {req_id}",
        "setup": f"HealthCore EHR test environment; test user logged in with appropriate role; {subsystem} module active",
        "steps": f"Step: 1. Navigate to {subsystem} module.\nStep: 2. Execute primary action specified in requirement.\nStep: 3. Verify system response and state change.",
        "expectedResults": f"ExpectedResult: 1. {subsystem} module loads successfully.\nExpectedResult: 2. Primary action completes without errors.\nExpectedResult: 3. System state updated correctly and audit log entry created."
    })
    
    # Negative TC (unless M2 N-A)
    if not use_m2_na:
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-B",
            "description": f"Verify error handling for invalid input in {req_id}",
            "setup": f"HealthCore EHR test environment; {subsystem} module active",
            "steps": f"Step: 1. Navigate to {subsystem} module.\nStep: 2. Provide invalid or malformed input.\nStep: 3. Attempt to submit action.",
            "expectedResults": f"ExpectedResult: 1. Module loads successfully.\nExpectedResult: 2. System validates input and detects error.\nExpectedResult: 3. Appropriate error message displayed and action blocked."
        })
    
    # Boundary TC (unless M3 N-A)
    if not use_m3_na:
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-C",
            "description": f"Verify boundary conditions for {req_id}",
            "setup": f"HealthCore EHR test environment; {subsystem} module active",
            "steps": f"Step: 1. Navigate to {subsystem} module.\nStep: 2. Test at threshold or boundary value.\nStep: 3. Verify system behavior at edge case.",
            "expectedResults": f"ExpectedResult: 1. Module loads successfully.\nExpectedResult: 2. Boundary value accepted or rejected appropriately.\nExpectedResult: 3. System handles edge case correctly per specification."
        })
    
    # Additional coverage TC for multi-spec requirements
    if len(specs) > 3:
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-D",
            "description": f"Verify audit logging and traceability for {req_id}",
            "setup": f"HealthCore EHR test environment; audit log viewer open",
            "steps": f"Step: 1. Execute action specified in requirement.\nStep: 2. Query audit log for recent entries.\nStep: 3. Verify log entry contains required fields.",
            "expectedResults": f"ExpectedResult: 1. Action completes successfully.\nExpectedResult: 2. Audit log entry created.\nExpectedResult: 3. Log entry includes timestamp, user ID, action type, and outcome."
        })
    
    return test_cases


def generate_test_cases_known_bad(
    req_id: str,
    specs: List[Dict[str, str]],
    subsystem: str,
    failure_dim: str,
    record_idx: int
) -> List[Dict[str, Any]]:
    """Generate test cases with deliberate gap for known-bad record."""
    test_cases = []
    tc_num = req_id.split("-")[-1]
    
    if failure_dim == "M1":
        # Missing functional TC - only negative/boundary
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-A",
            "description": f"Verify error handling for {req_id}",
            "setup": f"HealthCore EHR test environment; {subsystem} module active",
            "steps": f"Step: 1. Provide invalid input.\nStep: 2. Verify error response.",
            "expectedResults": f"ExpectedResult: 1. System rejects invalid input.\nExpectedResult: 2. Error message displayed."
        })
        
    elif failure_dim == "M2":
        # Missing negative TC - only functional
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-A",
            "description": f"Verify functional behavior for {req_id}",
            "setup": f"HealthCore EHR test environment; {subsystem} module active",
            "steps": f"Step: 1. Execute primary action.\nStep: 2. Verify success.",
            "expectedResults": f"ExpectedResult: 1. Action completes successfully.\nExpectedResult: 2. System state updated."
        })
        
    elif failure_dim == "M3":
        # Missing boundary TC - only functional/negative
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-A",
            "description": f"Verify functional behavior for {req_id}",
            "setup": f"HealthCore EHR test environment; {subsystem} module active",
            "steps": f"Step: 1. Execute primary action.\nStep: 2. Verify success.",
            "expectedResults": f"ExpectedResult: 1. Action completes successfully.\nExpectedResult: 2. System state updated."
        })
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-B",
            "description": f"Verify error handling for {req_id}",
            "setup": f"HealthCore EHR test environment; {subsystem} module active",
            "steps": f"Step: 1. Provide invalid input.\nStep: 2. Verify error response.",
            "expectedResults": f"ExpectedResult: 1. System rejects invalid input.\nExpectedResult: 2. Error message displayed."
        })
        
    elif failure_dim == "M4":
        # Missing coverage for one spec - functional TCs but incomplete
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-A",
            "description": f"Verify partial functionality for {req_id}",
            "setup": f"HealthCore EHR test environment; {subsystem} module active",
            "steps": f"Step: 1. Execute subset of required actions.\nStep: 2. Verify partial coverage.",
            "expectedResults": f"ExpectedResult: 1. Tested actions complete successfully.\nExpectedResult: 2. Partial system state updated."
        })
        
    elif failure_dim == "M5":
        # Terminology mismatch - use different vocabulary
        test_cases.append({
            "test_id": f"TC-HC-{tc_num}-A",
            "description": f"Verify standard operation for {req_id}",  # Vocabulary drift
            "setup": f"HealthCore EHR test environment; {subsystem} module active",
            "steps": f"Step: 1. Execute routine action.\nStep: 2. Verify normal processing.",  # Different terms
            "expectedResults": f"ExpectedResult: 1. Standard processing completes.\nExpectedResult: 2. Regular system state updated."  # Mismatched vocabulary
        })
    
    return test_cases


def build_coverage_analysis(
    specs: List[Dict[str, str]],
    test_cases: List[Dict[str, Any]],
    is_known_good: bool,
    failure_dim: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Build coverage analysis for specs."""
    coverage = []
    
    for i, spec in enumerate(specs):
        # For known-bad M4 failures, leave last spec uncovered
        if not is_known_good and failure_dim == "M4" and i == len(specs) - 1:
            coverage.append({
                "spec_id": spec["spec_id"],
                "covered_exists": False,
                "covered_by_test_cases": []
            })
        else:
            # Covered by test cases
            covering_tcs = []
            for tc in test_cases[:min(len(test_cases), 3)]:
                dimensions = []
                desc_lower = tc["description"].lower()
                
                if "functional" in desc_lower or "verify" in desc_lower or "behavior" in desc_lower:
                    dimensions.append("functional")
                if "error" in desc_lower or "invalid" in desc_lower or "handling" in desc_lower:
                    dimensions.append("negative")
                if "boundary" in desc_lower or "threshold" in desc_lower or "edge" in desc_lower:
                    dimensions.append("boundary")
                
                if not dimensions:
                    dimensions = ["functional"]  # Default
                
                covering_tcs.append({
                    "test_case_id": tc["test_id"],
                    "dimensions": dimensions,
                    "rationale": f"{tc['test_id']} covers {spec['spec_id']} via {', '.join(dimensions)} testing"
                })
            
            coverage.append({
                "spec_id": spec["spec_id"],
                "covered_exists": True,
                "covered_by_test_cases": covering_tcs
            })
    
    return coverage


def build_test_suite_summary(test_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build test suite summary."""
    summary = []
    for tc in test_cases:
        summary.append({
            "test_case_id": tc["test_id"],
            "objective": tc["description"],
            "verifies": "Requirement specification",
            "protocol": [s.strip() for s in tc.get("steps", "").split("\n") if s.strip()],
            "acceptance_criteria": [s.strip() for s in tc.get("expectedResults", "").split("\n") if s.strip()],
            "is_generated": False
        })
    return summary


def build_synthesized_assessment_known_good(
    requirement: Dict[str, str],
    specs: List[Dict[str, str]],
    coverage: List[Dict[str, Any]],
    use_m2_na: bool,
    use_m3_na: bool
) -> Dict[str, Any]:
    """Build synthesized assessment for known-good."""
    # Extract TC IDs from coverage
    functional_tcs = set()
    negative_tcs = set()
    boundary_tcs = set()
    
    for cov in coverage:
        for tc in cov["covered_by_test_cases"]:
            if "functional" in tc["dimensions"]:
                functional_tcs.add(tc["test_case_id"])
            if "negative" in tc["dimensions"]:
                negative_tcs.add(tc["test_case_id"])
            if "boundary" in tc["dimensions"]:
                boundary_tcs.add(tc["test_case_id"])
    
    mandatory_findings = [
        {
            "code": "M1",
            "dimension": "Functional",
            "verdict": "Yes",
            "partial": False,
            "rationale": f"Functional behavior verified by {', '.join(sorted(functional_tcs))}",
            "cited_test_case_ids": sorted(list(functional_tcs)),
            "uncovered_spec_ids": []
        },
        {
            "code": "M2",
            "dimension": "Negative",
            "verdict": "N-A" if use_m2_na else "Yes",
            "partial": False,
            "rationale": "No validation surface in this requirement" if use_m2_na else f"Error paths verified by {', '.join(sorted(negative_tcs))}",
            "cited_test_case_ids": [] if use_m2_na else sorted(list(negative_tcs)),
            "uncovered_spec_ids": []
        },
        {
            "code": "M3",
            "dimension": "Boundary",
            "verdict": "N-A" if use_m3_na else "Yes",
            "partial": False,
            "rationale": "No threshold or limit in this requirement" if use_m3_na else f"Boundary conditions verified by {', '.join(sorted(boundary_tcs))}",
            "cited_test_case_ids": [] if use_m3_na else sorted(list(boundary_tcs)),
            "uncovered_spec_ids": []
        },
        {
            "code": "M4",
            "dimension": "Spec Coverage",
            "verdict": "Yes",
            "partial": False,
            "rationale": "All specs covered",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": []
        },
        {
            "code": "M5",
            "dimension": "Terminology",
            "verdict": "Yes",
            "partial": False,
            "rationale": "Aligned",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": []
        }
    ]
    
    return {
        "requirement": requirement,
        "overall_verdict": "Yes",
        "mandatory_findings": mandatory_findings,
        "comments": "",
        "clarification_questions": []
    }


def build_synthesized_assessment_known_bad(
    requirement: Dict[str, str],
    specs: List[Dict[str, str]],
    coverage: List[Dict[str, Any]],
    failure_dim: str
) -> Dict[str, Any]:
    """Build synthesized assessment for known-bad."""
    # Extract TC IDs from coverage
    functional_tcs = set()
    negative_tcs = set()
    boundary_tcs = set()
    
    for cov in coverage:
        for tc in cov["covered_by_test_cases"]:
            if "functional" in tc["dimensions"]:
                functional_tcs.add(tc["test_case_id"])
            if "negative" in tc["dimensions"]:
                negative_tcs.add(tc["test_case_id"])
            if "boundary" in tc["dimensions"]:
                boundary_tcs.add(tc["test_case_id"])
    
    # Find uncovered specs for M4
    uncovered_specs = [cov["spec_id"] for cov in coverage if not cov["covered_exists"]]
    
    # Build findings based on failure dimension
    findings_templates = {
        "M1": {
            "M1": {"verdict": "No", "rationale": "No functional test cases verify the core positive behavior", "cited": [], "uncovered": []},
            "M2": {"verdict": "Yes" if negative_tcs else "N-A", "rationale": f"Error paths verified by {', '.join(sorted(negative_tcs))}" if negative_tcs else "No validation surface", "cited": sorted(list(negative_tcs)), "uncovered": []},
            "M3": {"verdict": "N-A", "rationale": "No threshold or limit in this requirement", "cited": [], "uncovered": []},
            "M4": {"verdict": "Yes", "rationale": "All specs covered", "cited": [], "uncovered": []},
            "M5": {"verdict": "Yes", "rationale": "Aligned", "cited": [], "uncovered": []}
        },
        "M2": {
            "M1": {"verdict": "Yes", "rationale": f"Functional behavior verified by {', '.join(sorted(functional_tcs))}", "cited": sorted(list(functional_tcs)), "uncovered": []},
            "M2": {"verdict": "No", "rationale": "No negative test cases verify error handling or validation", "cited": [], "uncovered": []},
            "M3": {"verdict": "N-A", "rationale": "No threshold or limit in this requirement", "cited": [], "uncovered": []},
            "M4": {"verdict": "Yes", "rationale": "All specs covered", "cited": [], "uncovered": []},
            "M5": {"verdict": "Yes", "rationale": "Aligned", "cited": [], "uncovered": []}
        },
        "M3": {
            "M1": {"verdict": "Yes", "rationale": f"Functional behavior verified by {', '.join(sorted(functional_tcs))}", "cited": sorted(list(functional_tcs)), "uncovered": []},
            "M2": {"verdict": "Yes" if negative_tcs else "N-A", "rationale": f"Error paths verified by {', '.join(sorted(negative_tcs))}" if negative_tcs else "No validation surface", "cited": sorted(list(negative_tcs)), "uncovered": []},
            "M3": {"verdict": "No", "rationale": "No boundary test cases verify threshold or limit conditions", "cited": [], "uncovered": []},
            "M4": {"verdict": "Yes", "rationale": "All specs covered", "cited": [], "uncovered": []},
            "M5": {"verdict": "Yes", "rationale": "Aligned", "cited": [], "uncovered": []}
        },
        "M4": {
            "M1": {"verdict": "Yes", "rationale": f"Functional behavior verified by {', '.join(sorted(functional_tcs))}", "cited": sorted(list(functional_tcs)), "uncovered": []},
            "M2": {"verdict": "N-A", "rationale": "No validation surface in this requirement", "cited": [], "uncovered": []},
            "M3": {"verdict": "N-A", "rationale": "No threshold or limit in this requirement", "cited": [], "uncovered": []},
            "M4": {"verdict": "No", "rationale": "One or more specs lack covering test cases", "cited": [], "uncovered": uncovered_specs},
            "M5": {"verdict": "Yes", "rationale": "Aligned", "cited": [], "uncovered": []}
        },
        "M5": {
            "M1": {"verdict": "Yes", "rationale": f"Functional behavior verified by {', '.join(sorted(functional_tcs))}", "cited": sorted(list(functional_tcs)), "uncovered": []},
            "M2": {"verdict": "N-A", "rationale": "No validation surface in this requirement", "cited": [], "uncovered": []},
            "M3": {"verdict": "N-A", "rationale": "No threshold or limit in this requirement", "cited": [], "uncovered": []},
            "M4": {"verdict": "Yes", "rationale": "All specs covered", "cited": [], "uncovered": []},
            "M5": {"verdict": "No", "rationale": "Test case vocabulary drifts from requirement terminology", "cited": [], "uncovered": []}
        }
    }
    
    template = findings_templates[failure_dim]
    mandatory_findings = []
    
    for code in ["M1", "M2", "M3", "M4", "M5"]:
        finding_data = template[code]
        finding = {
            "code": code,
            "dimension": {"M1": "Functional", "M2": "Negative", "M3": "Boundary", "M4": "Spec Coverage", "M5": "Terminology"}[code],
            "verdict": finding_data["verdict"],
            "partial": False,
            "rationale": finding_data["rationale"],
            "cited_test_case_ids": finding_data.get("cited", []),
            "uncovered_spec_ids": finding_data.get("uncovered", [])
        }
        mandatory_findings.append(finding)
    
    gap_comments = {
        "M1": "Test suite lacks functional test cases verifying the core positive behavior.",
        "M2": "Test suite lacks negative test cases verifying error handling and validation paths.",
        "M3": "Test suite lacks boundary test cases verifying threshold and limit conditions.",
        "M4": "Test suite has incomplete spec coverage with one or more decomposed specifications untested.",
        "M5": "Test case vocabulary drifts from requirement terminology, creating semantic misalignment."
    }
    
    return {
        "requirement": requirement,
        "overall_verdict": "No",
        "mandatory_findings": mandatory_findings,
        "comments": gap_comments[failure_dim],
        "clarification_questions": []
    }


def validate_record(output_record: Dict[str, Any]) -> bool:
    """Validate output record against schema."""
    try:
        # Validate synthesized assessment
        SynthesizedAssessment.model_validate(output_record["synthesized_assessment"])
        
        # Check overall_verdict consistency
        assessment = output_record["synthesized_assessment"]
        findings = assessment["mandatory_findings"]
        
        # overall_verdict should be "Yes" iff all verdicts in {Yes, N-A}
        expected_verdict = "Yes" if all(f["verdict"] in ["Yes", "N-A"] for f in findings) else "No"
        if assessment["overall_verdict"] != expected_verdict:
            print(f"ERROR: overall_verdict mismatch for {output_record['requirement']['req_id']}")
            return False
        
        # Check exactly 5 findings in M1-M5 order
        if len(findings) != 5:
            print(f"ERROR: Expected 5 findings, got {len(findings)} for {output_record['requirement']['req_id']}")
            return False
        
        for i, code in enumerate(["M1", "M2", "M3", "M4", "M5"]):
            if findings[i]["code"] != code:
                print(f"ERROR: Finding {i} should be {code}, got {findings[i]['code']}")
                return False
        
        # Check partial=true only with verdict=Yes
        for finding in findings:
            if finding["partial"] and finding["verdict"] != "Yes":
                print(f"ERROR: partial=true with verdict={finding['verdict']} in {finding['code']}")
                return False
        
        return True
        
    except Exception as e:
        print(f"Validation error: {e}")
        return False


def generate_batch(batch_num: int) -> tuple[List[Dict], List[Dict]]:
    """Generate one batch of 50 records (25 known-good, 25 known-bad)."""
    inputs = []
    outputs = []
    
    start_idx = (batch_num - 1) * RECORDS_PER_BATCH
    
    for i in range(RECORDS_PER_BATCH):
        record_idx = start_idx + i + 100  # Start from REQ-HC-100
        
        # Alternate between known-good and known-bad
        is_known_good = (i % 2 == 0)
        
        # Determine subsystem
        subsystem = get_subsystem_for_record(record_idx)
        
        # Generate requirement
        req_id = f"REQ-HC-{record_idx:03d}"
        req_text = generate_requirement_text(subsystem, req_id, record_idx)
        requirement = {"req_id": req_id, "text": req_text}
        
        # Decompose into specs
        num_specs = 3 + (record_idx % 3)  # 3, 4, or 5 specs
        specs = generate_decomposed_specs(req_id, req_text, num_specs, subsystem)
        
        if is_known_good:
            # Determine M2/M3 N-A status
            use_m2_na = (record_idx % 20 == 0)  # 5% frequency
            use_m3_na = (record_idx % 5 == 0)   # 20% frequency
            
            # Generate test cases
            test_cases = generate_test_cases_known_good(req_id, specs, subsystem, use_m2_na, use_m3_na, record_idx)
            
            # Build coverage analysis
            coverage = build_coverage_analysis(specs, test_cases, True, None)
            
            # Build test suite summary
            test_suite_summary = build_test_suite_summary(test_cases)
            
            # Build synthesized assessment
            synthesized_assessment = build_synthesized_assessment_known_good(requirement, specs, coverage, use_m2_na, use_m3_na)
            
            # Build input record
            input_record = {
                "requirement": requirement,
                "test_cases": test_cases,
                "rationale": "This test suite fully verifies the requirement across functional, negative, and boundary dimensions with complete spec coverage.",
                "expected_gap": "none",
                "description": "Complete coverage with no gaps identified."
            }
        else:
            # Cycle through M1-M5 failures
            failure_dim = FAILURE_DIMS[record_idx % 5]
            
            # Generate test cases with gap
            test_cases = generate_test_cases_known_bad(req_id, specs, subsystem, failure_dim, record_idx)
            
            # Build coverage analysis
            coverage = build_coverage_analysis(specs, test_cases, False, failure_dim)
            
            # Build test suite summary
            test_suite_summary = build_test_suite_summary(test_cases)
            
            # Build synthesized assessment
            synthesized_assessment = build_synthesized_assessment_known_bad(requirement, specs, coverage, failure_dim)
            
            # Map failure dimension to expected_gap
            gap_mapping = {"M1": "functional", "M2": "negative", "M3": "boundary", "M4": "coverage", "M5": "terminology"}
            
            # Build input record
            input_record = {
                "requirement": requirement,
                "test_cases": test_cases,
                "rationale": synthesized_assessment["comments"],
                "expected_gap": gap_mapping[failure_dim],
                "description": f"Test suite has a {gap_mapping[failure_dim]} coverage gap."
            }
        
        # Build output record
        output_record = {
            "requirement": requirement,
            "test_cases": test_cases,
            "decomposed_requirement": {
                "requirement": requirement,
                "decomposed_specifications": specs
            },
            "test_suite": {
                "requirement": requirement,
                "test_cases": test_cases,
                "summary": test_suite_summary
            },
            "coverage_analysis": coverage,
            "synthesized_assessment": synthesized_assessment
        }
        
        inputs.append(input_record)
        outputs.append(output_record)
    
    return inputs, outputs


def main():
    parser = argparse.ArgumentParser(description="Generate HealthCore EHR RTM dataset batch")
    parser.add_argument("--batch", type=int, required=True, help="Batch number (1-16)")
    parser.add_argument("--validate-only", action="store_true", help="Only validate existing batch")
    
    args = parser.parse_args()
    
    if args.batch < 1 or args.batch > TOTAL_BATCHES:
        print(f"ERROR: Batch number must be between 1 and {TOTAL_BATCHES}")
        return 1
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print(f"Generating batch {args.batch}/{TOTAL_BATCHES} for HealthCore EHR Suite...")
    print(f"  - Records per batch: {RECORDS_PER_BATCH} (25 known-good, 25 known-bad)")
    print(f"  - Subsystem distribution: Vitals 35%, CDS 35%, ePrescribing 15%, AccessControl 10%, DataMgmt 5%")
    
    inputs, outputs = generate_batch(args.batch)
    
    print(f"Generated {len(inputs)} input records and {len(outputs)} output records")
    
    # Validate all outputs
    print("Validating records...")
    valid_count = 0
    for i, output in enumerate(outputs):
        if validate_record(output):
            valid_count += 1
        else:
            print(f"Validation failed for record {i} ({output['requirement']['req_id']})")
    
    print(f"Validated {valid_count}/{len(outputs)} records successfully")
    
    if valid_count != len(outputs):
        print("ERROR: Some records failed validation. Not writing files.")
        return 1
    
    # Write batch files
    batch_inputs_file = OUTPUT_DIR / f"inputs_batch_{args.batch:02d}.jsonl"
    batch_outputs_file = OUTPUT_DIR / f"outputs_batch_{args.batch:02d}.jsonl"
    
    with open(batch_inputs_file, "w") as f:
        for record in inputs:
            f.write(json.dumps(record) + "\n")
    
    with open(batch_outputs_file, "w") as f:
        for record in outputs:
            f.write(json.dumps(record) + "\n")
    
    print(f"\nWrote batch files:")
    print(f"  - {batch_inputs_file}")
    print(f"  - {batch_outputs_file}")
    
    # Print batch statistics
    known_good = sum(1 for inp in inputs if inp["expected_gap"] == "none")
    known_bad = len(inputs) - known_good
    
    failure_counts = {}
    for inp in inputs:
        if inp["expected_gap"] != "none":
            failure_counts[inp["expected_gap"]] = failure_counts.get(inp["expected_gap"], 0) + 1
    
    print(f"\nBatch {args.batch} statistics:")
    print(f"  - Known-good: {known_good}")
    print(f"  - Known-bad: {known_bad}")
    print(f"  - Failure distribution: {failure_counts}")
    
    return 0


if __name__ == "__main__":
    exit(main())
