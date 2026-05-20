import re
import json
import pandas as pd
from typing import List, Dict


def extract_gids(text: str, format: str = "GID-\\d+") -> List[str]:
    """Extract all GID-\\d+ references from text."""
    if not isinstance(text, str):
        return []
    return re.findall(rf"{format}", text)


from typing import List, Dict, Any
import pandas as pd

def parse_sha_excel(
    file_path: str,
    sheet_name: str = "SHA_Table",
    extract_gids_format: str = "GID-\\d+"
) -> Dict[str, Any]:
    """
    Parse the SHA Excel file and return hazard data along with global control references.

    Args:
        file_path: Path to the Excel file.
        sheet_name: Excel sheet name (default = "SHA_Table").
        extract_gids_format: Regex pattern for extracting GIDs.

    Returns:
        A dictionary containing:
        - 'rows': List of hazard dicts, one per row.
        - 'all_controls_references': A sorted list of all unique GIDs extracted 
                                     from the Risk Control Measures columns.
    """
    df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    rcm_columns = [col for col in df.columns if "Risk Control Measures" in col]

    all_gids_set = set()
    results = []

    for _, row in df.iterrows():
        # 1. Combine text strictly from the Risk Control Measures columns
        rcm_text = " ".join([str(row.get(col, "")) for col in rcm_columns if pd.notna(row.get(col))])
        
        # 2. Extract GIDs for this specific row using the provided format
        row_gids = extract_gids(rcm_text, extract_gids_format)
        
        # 3. Add to the global set of all unique GIDs
        all_gids_set.update(row_gids)

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
            "risk_control_measures": rcm_text.strip(),
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
        }
        results.append(hazard_item)

    # Return as a dictionary to keep `all_controls_references` alongside the rows
    return {
        "rows": results,
        "all_controls_references": sorted(list(all_gids_set))
    }


def parse_sha_excel_to_jsonl(
    file_path: str,
    output_path: str,
    sheet_name: str = "SHA_Table",
    extract_gids_format: str = "GID-\\d+",
) -> Dict[str, Any]:
    """
    Parse the SHA Excel file into JSONL format with additional control reference fields.

    Args:
        file_path: Path to the Excel file
        output_path: Path to output JSONL file
        sheet_name: Excel sheet name (default = "SHA Table")
    """
    results = parse_sha_excel(file_path, sheet_name, extract_gids_format)

    with open(output_path, "w", encoding="utf-8") as f:
        for item in results["rows"]:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"JSONL written to: {output_path}")
    return results


def build_traceability_jsonl(
    excel_rows, 
    identifiers, 
    identifiers_upstream_links, 
    identifier_downstream_links, 
    output_filename="output.jsonl"
):
    """
    Assembles hazard rows with their respective JAMA traceability data and writes to a JSONL file.
    
    :param excel_rows: List of dictionaries (the parsed Excel data)
    :param identifiers: List of unique JAMA requirement identifiers from column `risk_control_measures` of the input Excel.
    :param identifiers_upstream_links: Response from pyjama-fastapi endpoint (get_hierarchical_trace_from_gids) 
                                       containing upstream links for all unique identifiers in Excel file column `risk_control_measures`
    :param identifier_downstream_links: Response from pyjama-fastapi endpoint (get_hierarchical_trace_from_gids_downstream) 
                                         containing downstream links for all unique identifiers in Excel file column `risk_control_measures`
    """
    
    # 1. Build a master lookup dictionary for ALL unique identifiers found in the endpoints.
    # This aligns upstream (system reqs, user needs) and downstream (tests, design docs) by req_id.
    jama_lookup = {}
    
    # Process upstream links (e.g., system_requirements, user_needs)
    for item in identifiers_upstream_links:
        req_id = item.get("requirement", {}).get("req_id")
        if not req_id:
            continue
        if req_id not in jama_lookup:
            jama_lookup[req_id] = {
                "requirement": item.get("requirement"),
                "system_requirements": [],
                "test_cases": [],
                "design_docs": []
            }
        jama_lookup[req_id]["system_requirements"].extend(item.get("system_requirements", []))

    # Process downstream links (e.g., test_cases, design_docs)
    for item in identifier_downstream_links:
        req_id = item.get("requirement", {}).get("req_id")
        if not req_id:
            continue
        if req_id not in jama_lookup:
            jama_lookup[req_id] = {
                "requirement": item.get("requirement"),
                "system_requirements": [],
                "test_cases": [],
                "design_docs": []
            }
        # Safely capture the primary requirement text if it wasn't caught in the upstream payload
        if not jama_lookup[req_id]["requirement"]:
            jama_lookup[req_id]["requirement"] = item.get("requirement")
            
        jama_lookup[req_id]["test_cases"].extend(item.get("test_cases", []))
        jama_lookup[req_id]["design_docs"].extend(item.get("design_docs", []))

    # 2. Open the file and isolate data row-by-row
    with open(output_filename, 'w', encoding='utf-8') as f:
        for row in excel_rows.get("rows"):
            output_row = row.copy()
            output_row["requirements_traceability"] = []
            
            risk_control_text = row.get("risk_control_measures", "")
            
            # Identify which of the master 'identifiers' list actually exist in this specific row's text
            # This safely filters out the bulk API response down to only the row-relevant items
            row_identifiers = [
                req_id for req_id in identifiers 
                if req_id in risk_control_text
            ]
            
            # 3. Append only the relevant merged trace records for this specific row
            for req_id in row_identifiers:
                if req_id in jama_lookup:
                    output_row["requirements_traceability"].append(jama_lookup[req_id])
            
            # 4. Stream directly to the JSONL format
            f.write(json.dumps(output_row) + '\n')


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