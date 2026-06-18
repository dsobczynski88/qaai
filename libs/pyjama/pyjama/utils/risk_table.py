import io
import re
from typing import Literal, Union, Dict, List, Any
import pandas as pd


_GID_RE = re.compile(r"\b(GID-\d+)\b")
_DOCKEY_RE = re.compile(r"\b([A-Z]+-(?:PRQ|DES)-\d+)\b")


SHA_COLUMN_MAP: Dict[str, str] = {
    "Hazardous Situation ID": "hazardous_situation_id",
    "Hazard": "hazard",
    "Hazardous Situation": "hazardous_situation",
    "Function": "function",
    "OTS Software (if OTS, identify component)": "ots_software",
    "Hazardous sequence of events": "hazardous_sequence_of_events",
    "SHA ID Number": "hazard_id",
    "S/W Related Cause(s) - need to have separate rows.": "software_related_causes",
    "Harm Severity Rationale (External Risk Controls)": "harm_severity_rationale",
    "Harm": "harm",
    "Severity": "severity",
    "Exploitability - (Cyber) (Pre-Mitigation)": "exploitability_pre_mitigation",
    "Probability of Harm (software/Use-Related) (Pre-Mitigation)": "probability_of_harm_pre_mitigation",
    "Initial Risk Rating": "initial_risk_rating",
    "Risk Control Measures: Inherent Safety by Design and Manufacture; Protective Measures; Information for Safety": "risk_control_measures",
    "Demonstration of Effectiveness (Trace to Verification)": "demonstration_of_effectiveness",
    "Severity of Harm (Post-Mitigation)": "severity_of_harm_post_mitigation",
    "Exploitability - (Cyber)": "exploitability_post_mitigation",
    "Probability of Harm (software/Use-Related)": "probability_of_harm_post_mitigation",
    "Final Risk Rating": "final_risk_rating",
    "New HS if applicable – If yes, reference new row with SHA ID": "new_hs_reference",
    "SW FMEA Trace": "sw_fmea_trace",
    "SRA Link": "sra_link",
    "URRA Item": "urra_item",
    "Residual Risk Acceptability (Rationale for Acceptability per GQP-10-02, Risk Management Report)": "residual_risk_acceptability",
}


RAC_COLUMN_MAP: Dict[str, str] = {
    "ID": "hazard_id",
    "Hazard": "hazard",
    "Hazardous Situation": "hazardous_situation",
    "Foreseeable Sequence of Events": "hazardous_sequence_of_events",
    "Harm": "harm",
    "Severity of Harm": "severity",
    "Initial Likelihood of Harm": "probability_of_harm_pre_mitigation",
    "Initial Risk Evaluation": "initial_risk_rating",
    "Inherent Safety by Design and Manufacture": "risk_control_measures",
    "Verification of Effectiveness": "demonstration_of_effectiveness",
    "Residual Likelihood of Harm": "probability_of_harm_post_mitigation",
    "Residual Risk Evaluation": "final_risk_rating",
    "Individual Risk Acceptability": "residual_risk_acceptability",
}


HAZARD_STRING_FIELDS = (
    "hazard_id", "hazardous_situation_id", "hazard", "hazardous_situation",
    "function", "ots_software", "hazardous_sequence_of_events",
    "software_related_causes", "harm_severity_rationale", "harm", "severity",
    "exploitability_pre_mitigation", "probability_of_harm_pre_mitigation",
    "initial_risk_rating", "risk_control_measures",
    "demonstration_of_effectiveness", "severity_of_harm_post_mitigation",
    "exploitability_post_mitigation", "probability_of_harm_post_mitigation",
    "final_risk_rating", "new_hs_reference", "sw_fmea_trace", "sra_link",
    "urra_item", "residual_risk_acceptability",
)


def extract_mitigation_refs(cell: Any) -> Dict[str, List[str]]:
    """Pull GID and document-key references out of a free-text mitigation cell.

    Returns a dict with two sorted, de-duplicated lists:
      - global_ids: matches like "GID-12345"
      - doc_keys:   matches like "SYS-PRQ-1234" or "SYS-DES-99"
    """
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return {"global_ids": [], "doc_keys": []}
    text = str(cell)
    return {
        "global_ids": sorted(set(_GID_RE.findall(text))),
        "doc_keys": sorted(set(_DOCKEY_RE.findall(text))),
    }


def parse_risk_table(
    source: Union[bytes, str, "io.IOBase"],
    kind: Literal["SHA", "RAC"],
) -> List[Dict[str, Any]]:
    """Parse an SHA or RAC risk-table Excel into normalized hazard dicts.

    Args:
        source: Raw bytes (from an upload), a filesystem path, or a file-like.
        kind: "SHA" or "RAC" — determines which column map to apply.

    Returns:
        List of hazard dicts; each contains all HazardRecord string fields
        (missing source columns default to "") plus a "_refs" key with the
        extracted GIDs and document keys from the mitigation column.
    """
    if isinstance(source, (bytes, bytearray)):
        df = pd.read_excel(io.BytesIO(source))
    else:
        df = pd.read_excel(source)

    colmap = SHA_COLUMN_MAP if kind == "SHA" else RAC_COLUMN_MAP
    df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})

    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        rec: Dict[str, Any] = {}
        for field in HAZARD_STRING_FIELDS:
            value = row.get(field, "") if field in row.index else ""
            if value is None or (isinstance(value, float) and pd.isna(value)):
                rec[field] = ""
            else:
                rec[field] = str(value)
        rec["_refs"] = extract_mitigation_refs(rec["demonstration_of_effectiveness"])
        rows.append(rec)
    return rows
