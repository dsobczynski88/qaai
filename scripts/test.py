from autoqa.components.hazard_risk_reviewer.loader import build_traceability_jsonl, parse_sha_excel_to_jsonl

# ==========================================
# Input Data Definition
# ==========================================

excel_rows = [
    {
      "hazard_id": "HAZ-PUMP-001",
      "hazardous_situation_id": "HS-PUMP-001",
      "hazard": "Over-infusion of medication due to software loop hang",
      "hazardous_situation": "Patient receives medication at the maximum pump rate continuously, exceeding the prescribed dose, while the pump's UI fails to indicate the runaway condition.",
      "function": "Continuous infusion rate control loop",
      "ots_software": "FreeRTOS 10.4.3",
      "hazardous_sequence_of_events": "1. Periodic timer ISR fails to fire due to scheduler stall. 2. Rate-control loop continues to issue motor pulses at the most-recently-commanded rate. 3. UI thread, running on a separate task, continues to display the nominal infusion-rate display. 4. Pump delivers medication at the maximum commanded rate until manually halted.",
      "software_related_causes": "Scheduler stall under heavy task load; missing independent watchdog on the rate-control loop; UI thread not gated on heartbeat from rate-control task.",
      "harm_severity_rationale": "External risk controls (clinician monitoring, infusion bag volume limit) reduce but do not eliminate the chance that an over-infusion of insulin or chemotherapy reaches a clinically significant dose before detection.",
      "harm": "Severe over-infusion with potential for life-threatening overdose (insulin shock, cytotoxicity).",
      "severity": "Catastrophic",
      "exploitability_pre_mitigation": "Not applicable (not a cyber-exploitable surface)",
      "probability_of_harm_pre_mitigation": "Probable",
      "initial_risk_rating": "Unacceptable",
      "risk_control_measures": "REQ-PUMP-101 mandates the rate-control loop is monitored by an independent hardware watchdog that latches the motor driver into a safe state if heartbeats are missed for more than 200 ms.",
      "demonstration_of_effectiveness": "Verified by TC-PUMP-201 (functional heartbeat), TC-PUMP-202 (fault injection — scheduler stall), and TC-PUMP-203 (boundary — heartbeat exactly at 200 ms latency).",
      "severity_of_harm_post_mitigation": "Catastrophic",
      "exploitability_post_mitigation": "Not applicable",
      "probability_of_harm_post_mitigation": "Remote",
      "final_risk_rating": "Acceptable",
      "new_hs_reference": "",
      "sw_fmea_trace": "FMEA-PUMP-RC-001",
      "sra_link": "SRA-PUMP-2025-12",
      "urra_item": "URRA-PUMP-RC-001",
      "residual_risk_acceptability": "Per GQP-10-02 Risk Management Report, residual risk is acceptable: the hardware watchdog and UI alarm provide redundant detection and shutoff, and the post-mitigation probability of harm is Remote with a verified detection latency under 200 ms.",
      "row_specific_controls_references": ["REQ-PUMP-1"]
    }
]

identifiers = ["REQ-PUMP-101"]

# Note: Response 2 mapped to upstream links
identifiers_upstream_links = [
  {
    "requirement": {
      "req_id": "REQ-PUMP-101",
      "text": "The rate-control loop shall execute at 10 Hz..."
    },
    "system_requirements": [
        {
          "req_id": "SYS-PUMP-015",
          "text": "The infusion system shall include independent hardware and software watchdog mechanisms to detect and respond to software failures within 200 ms.",
          "user_needs": [
            {
              "req_id": "UN-PUMP-003",
              "text": "The infusion system shall prevent over-infusion that could harm the patient."
            }
          ]
        },
        {
          "req_id": "SYS-PUMP-016",
          "text": "The infusion system shall latch the motor driver into a safe (no-pulse) state when a software failure is detected by the watchdog mechanism.",
          "user_needs": [
            {
              "req_id": "UN-PUMP-003",
              "text": "The infusion system shall prevent over-infusion that could harm the patient."
            }
          ]
        },
        {
          "req_id": "SYS-PUMP-017",
          "text": "The infusion system shall provide visual and audible alarms to clinicians when a safety-critical failure is detected, including failures of the rate-control loop.",
          "user_needs": [
            {
              "req_id": "UN-PUMP-007",
              "text": "The infusion system shall provide clear and immediate feedback to clinicians when a safety-critical failure is detected."
            }
          ]
        }
    ]
  }
]

# Note: Response 1 mapped to downstream links
identifier_downstream_links = [
  {
    "requirement": {
      "req_id": "REQ-PUMP-101",
      "text": "The rate-control loop shall execute at 10 Hz..."
    },
    "test_cases": [
      {
        "test_id": "TC-PUMP-202",
        "description": "Fault injection — simulate scheduler stall...",
        "setup": "Pump in standard infusion mode...",
        "steps": "Step 1. Start an infusion at 5 mL/hr...\nStep 2. ...",
        "expectedResults": "ExpectedResult 1. Watchdog counter stalls...\nExpectedResult 2. ...",
        "in_review_baseline": False
      }
    ],
    "design_docs": [
      {
        "doc_id": "DD-PUMP-RC-001",
        "name": "Rate Control Loop Design",
        "description": "Detailed design of the rate control loop..."
      }
    ]
  }
]

# ==========================================
# Execution
# ==========================================

if __name__ == "__main__":
    output_file = "hazard_traceability_output.jsonl"
    
    excel_rows = parse_sha_excel_to_jsonl(
        file_path="./tests/fixtures/external/software_hazard_analysis.xlsx",
        output_path="data/results.jsonl",
        sheet_name="SHA_Table",
        extract_gids_format="REQ-PUMP-\\d+",
    )
    print(excel_rows)
    
    build_traceability_jsonl(
        excel_rows=excel_rows,
        identifiers=identifiers,
        identifiers_upstream_links=identifiers_upstream_links,
        identifier_downstream_links=identifier_downstream_links,
        output_filename=output_file
    )
    print(f"Traceability JSONL successfully written to {output_file}")