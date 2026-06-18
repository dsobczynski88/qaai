"""Constants for the hazard risk reviewer.

``HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS`` lists the SHA (Software Hazard
Analysis) fields a hazard record must populate for a review to be meaningful.
The input gate (see ``qaai.agents.shared.gate`` and ``validate_hazard_inputs``
in ``nodes.py``) checks each of these against the in-state
``HazardRowWithTraceMatrix``; any that are blank cause the graph to skip the
record (no LLM calls) and surface a missing-fields warning in the viewer.

Each name is a snake_case attribute on ``HazardRowFromExcel`` (see ``core.py``),
mapped from its Excel column header via a Pydantic alias.
"""

HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS = [
    "hazard_id",
    "hazardous_situation_id",
    "hazard",
    "hazardous_situation",
    "function",
    "hazardous_sequence_of_events",
    "harm",
    "severity",
    "probability_of_harm_pre_mitigation",
    "initial_risk_rating",
    "risk_control_measures",
    "severity_of_harm_post_mitigation",
    "probability_of_harm_post_mitigation",
    "final_risk_rating",
    "residual_risk_acceptability",
]
