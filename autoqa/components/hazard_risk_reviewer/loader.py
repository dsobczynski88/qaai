import re
import json
import pandas as pd
from typing import List, Dict


def extract_gids(text: str) -> List[str]:
    """Extract all GID-\\d+ references from text."""
    if not isinstance(text, str):
        return []
    return re.findall(r"GID-\d+", text)


def parse_sha_excel(
    file_path: str,
    sheet_name: str = "SHA Table",
) -> List[dict]:
    """
    Parse the SHA Excel file and return a list of hazard dicts in memory.

    Args:
        file_path: Path to the Excel file.
        sheet_name: Excel sheet name (default = "SHA Table").

    Returns:
        List of hazard dicts, one per row.
    """
    df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    rcm_columns = [col for col in df.columns if "Risk Control Measures" in col]

    all_gids_set = set()
    for _, row in df.iterrows():
        for col in rcm_columns:
            all_gids_set.update(extract_gids(str(row[col])))
    all_gids_list = sorted(all_gids_set)

    results = []
    for _, row in df.iterrows():
        row_text = " ".join([str(v) for v in row.values if pd.notna(v)])
        row_gids = extract_gids(row_text)

        hazard_item = {
            "hazard_id": str(row.get("SHA ID Number", "")).strip(),
            "hazardous_situation_id": str(row.get("Hazardous Situation ID", "")).strip(),
            "hazard": str(row.get("Hazard", "")).strip(),
            "hazardous_situation": str(row.get("Hazardous Situation", "")).strip(),
            "function": str(row.get("Function", "")).strip(),
            "ots_software": str(row.get("OTS Software (if OTS, identify component)", "")).strip(),
            "hazardous_sequence_of_events": str(row.get("Hazardous sequence of events", "")).strip(),
            "software_related_causes": str(row.get("S/W Related Cause(s)", "")).strip(),
            "harm": str(row.get("Harm", "")).strip(),
            "severity": str(row.get("Severity", "")).strip(),
            "exploitability_pre_mitigation": str(row.get("Exploitability - (Cyber) (Pre-Mitigation)", "")).strip(),
            "probability_of_harm_pre_mitigation": str(row.get("Probability of Harm (software/Use-Related) (Pre-Mitigation)", "")).strip(),
            "initial_risk_rating": str(row.get("Initial Risk Rating", "")).strip(),
            "risk_control_measures": " ".join([
                str(row.get(col, "")).strip() for col in rcm_columns
            ]),
            "demonstration_of_effectiveness": str(row.get("Demonstration of Effectiveness (Trace to Verification)", "")).strip(),
            "severity_of_harm_post_mitigation": str(row.get("Severity of Harm (Post-Mitigation)", "")).strip(),
            "exploitability_post_mitigation": str(row.get("Exploitability - (Cyber)", "")).strip(),
            "probability_of_harm_post_mitigation": str(row.get("Probability of Harm (software/Use-Related)", "")).strip(),
            "final_risk_rating": str(row.get("Final Risk Rating", "")).strip(),
            "new_hs_reference": str(row.get("New HS if applicable If yes, reference new row with SHA ID", "")).strip(),
            "sw_fmea_trace": str(row.get("System DFMEA Trace", "")).strip(),
            "sra_link": str(row.get("SRA Link", "")).strip(),
            "urra_item": str(row.get("URRA Item", "")).strip(),
            "residual_risk_acceptability": str(row.get("Residual Risk Acceptability", "")).strip(),
            "row_specific_controls_references": sorted(set(row_gids)),
            "all_controls_references": all_gids_list,
        }
        results.append(hazard_item)

    return results


def hazard_dict_to_record(d: dict):
    """
    Convert a hazard dict (as produced by parse_sha_excel) to a HazardRecord.

    Fields not present in the Excel schema (harm_severity_rationale) default to
    empty string. Relational fields (requirements, test_cases, design_docs,
    user_needs, system_requirements) default to empty lists — callers can
    populate them separately after loading.
    """
    from autoqa.components.hazard_risk_reviewer.core import HazardRecord

    return HazardRecord(
        hazard_id=d.get("hazard_id", ""),
        hazardous_situation_id=d.get("hazardous_situation_id", ""),
        hazard=d.get("hazard", ""),
        hazardous_situation=d.get("hazardous_situation", ""),
        function=d.get("function", ""),
        ots_software=d.get("ots_software", ""),
        hazardous_sequence_of_events=d.get("hazardous_sequence_of_events", ""),
        software_related_causes=d.get("software_related_causes", ""),
        harm_severity_rationale=d.get("harm_severity_rationale", ""),
        harm=d.get("harm", ""),
        severity=d.get("severity", ""),
        exploitability_pre_mitigation=d.get("exploitability_pre_mitigation", ""),
        probability_of_harm_pre_mitigation=d.get("probability_of_harm_pre_mitigation", ""),
        initial_risk_rating=d.get("initial_risk_rating", ""),
        risk_control_measures=d.get("risk_control_measures", ""),
        demonstration_of_effectiveness=d.get("demonstration_of_effectiveness", ""),
        severity_of_harm_post_mitigation=d.get("severity_of_harm_post_mitigation", ""),
        exploitability_post_mitigation=d.get("exploitability_post_mitigation", ""),
        probability_of_harm_post_mitigation=d.get("probability_of_harm_post_mitigation", ""),
        final_risk_rating=d.get("final_risk_rating", ""),
        new_hs_reference=d.get("new_hs_reference", ""),
        sw_fmea_trace=d.get("sw_fmea_trace", ""),
        sra_link=d.get("sra_link", ""),
        urra_item=d.get("urra_item", ""),
        residual_risk_acceptability=d.get("residual_risk_acceptability", ""),
        requirements=[],
        test_cases=[],
        design_docs=[],
        user_needs=[],
        system_requirements=[],
    )


def parse_sha_excel_to_jsonl(
    file_path: str,
    output_path: str,
    sheet_name: str = "SHA Table",
) -> None:
    """
    Parse the SHA Excel file into JSONL format with additional control reference fields.

    Args:
        file_path: Path to the Excel file
        output_path: Path to output JSONL file
        sheet_name: Excel sheet name (default = "SHA Table")
    """
    results = parse_sha_excel(file_path, sheet_name)

    with open(output_path, "w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"JSONL written to: {output_path}")