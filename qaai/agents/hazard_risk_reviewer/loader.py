import logging
import re
import json
import pandas as pd
from typing import List, Dict, Any, Union

from .core import (
    HazardRowFromExcel,
    HazardPackageFromExcel,
    HazardTraceMatrix,
    HazardRowWithTraceMatrix,
)

logger = logging.getLogger(__name__)


def extract_gids(text: str, format: str = "GID-\\d+") -> List[str]:
    """Extract all GID-\\d+ references from text."""
    if not isinstance(text, str):
        return []
    return re.findall(rf"{format}", text)


def normalize_row_dict(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a row dictionary for model validation.
    
    Handles:
    - Converts NaN (float or numpy.nan) to empty strings
    - Ensures all string values are proper strings
    
    Args:
        row_dict: Dictionary from pandas row
        
    Returns:
        Normalized dictionary safe for Pydantic validation
    """
    normalized = {}
    for key, value in row_dict.items():
        # Handle NaN values (convert to empty string)
        if pd.isna(value):
            normalized[key] = ""
        # Convert to string if it's not already
        elif not isinstance(value, str) and not isinstance(value, list):
            normalized[key] = str(value)
        else:
            normalized[key] = value
    return normalized


def parse_sha_excel(
    file_path: str,
    sheet_name: str = "SHA Table",
    extract_gids_format: str = "GID-\\d+"
) -> HazardPackageFromExcel:
    """
    Parse the SHA Excel file and return hazard data along with global control references.

    Args:
        file_path: Path to the Excel file.
        sheet_name: Excel sheet name (default = "SHA_Table").
        extract_gids_format: Regex pattern for extracting GIDs.

    Returns:
        HazardPackageFromExcel containing:
        - rows: List of HazardRowFromExcel models, one per row.
        - all_controls_references: A sorted list of all unique GIDs extracted 
                                   from the Risk Control Measures columns.
    """
    df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
    # Normalize column names: remove newlines, collapse multiple spaces, strip whitespace
    df.columns = [' '.join(str(c).replace('\n', ' ').split()) for c in df.columns]

    rcm_columns = [col for col in df.columns if "Risk Control Measures" in col]
    logger.info(
        "parse_sha_excel: sheet=%r, %d rows, identifier pattern=%r, "
        "Risk Control Measures columns matched=%s",
        sheet_name, len(df), extract_gids_format, rcm_columns or "<none>",
    )
    if not rcm_columns:
        logger.warning(
            "parse_sha_excel: no column matched 'Risk Control Measures' in %s — "
            "no requirement identifiers can be extracted",
            [str(c) for c in df.columns],
        )

    all_gids_set = set()
    rows_with_text = 0
    results = []

    for _, row in df.iterrows():
        # 1. Combine text strictly from the Risk Control Measures columns
        rcm_text = " ".join([str(row.get(col, "")) for col in rcm_columns if pd.notna(row.get(col))])
        
        # 2. Extract GIDs for this specific row using the provided format
        row_gids = extract_gids(rcm_text, extract_gids_format)
        if rcm_text.strip():
            rows_with_text += 1

        # 3. Add to the global set of all unique GIDs
        all_gids_set.update(row_gids)

        # Convert row to dict for model validation, then add extracted GIDs
        row_dict = normalize_row_dict(row.to_dict())
        
        # Explicitly set risk_control_measures from the matched RCM columns
        # This ensures the field is populated even if the Excel column header
        # doesn't exactly match the Pydantic alias (e.g., multi-line headers)
        row_dict["risk_control_measures"] = rcm_text
        
        row_dict["row_specific_controls_references"] = sorted(set(row_gids))
        
        # Create HazardRowFromExcel model
        hazard_row = HazardRowFromExcel.model_validate(row_dict)
        results.append(hazard_row)

    all_controls = sorted(all_gids_set)
    logger.info(
        "parse_sha_excel: extracted %d unique requirement identifier(s) from %d row(s) "
        "with Risk Control Measures text",
        len(all_controls), rows_with_text,
    )
    if rows_with_text and not all_controls:
        # Text is present but the pattern matched nothing — the classic
        # wrong-prefix mistake (e.g. pattern 'GID-\\d+' against 'REQ-PUMP-101' text).
        sample = next(
            (r.risk_control_measures for r in results
             if getattr(r, "risk_control_measures", "").strip()),
            "",
        )
        logger.warning(
            "parse_sha_excel: %d row(s) have Risk Control Measures text but pattern %r "
            "matched ZERO identifiers - check the 'Requirements Prefix' regex. Sample text: %r",
            rows_with_text, extract_gids_format, sample[:200],
        )

    # Return as HazardPackageFromExcel model
    return HazardPackageFromExcel(
        rows=results,
        all_controls_references=all_controls,
    )


def merge_hazard_with_pyjama_traceability(
    excel_row: HazardRowFromExcel,
    pyjama_lookup: Dict[str, Any],
) -> HazardRowWithTraceMatrix:
    """
    Merge a single Excel-derived hazard row with unified pyjama traceability data.

    Filters pyjama responses to only those req_ids that appear in this row's
    row_specific_controls_references (which are extracted from the risk_control_measures text).

    Args:
        excel_row: HazardRowFromExcel model (from parse_sha_excel results).
                   Must contain 'row_specific_controls_references' field.
        pyjama_lookup: Dict indexed by req_id with unified bidirectional trace.
                       Each value has: requirement, system_requirements, test_cases, design_docs.

    Returns:
        HazardRowWithTraceMatrix with requirements_traceability field populated.
        The field contains a HazardTraceMatrix with only the pyjama items 
        whose req_id matches row_specific_controls_references.
    """
    # Get row-specific identifiers extracted from risk_control_measures
    row_ids = excel_row.row_specific_controls_references or []

    # Build requirements_traceability by collecting matching pyjama items
    requirements = []
    test_cases = []
    design_docs = []
    user_needs = []
    system_requirements = []

    for req_id in row_ids:
        if req_id in pyjama_lookup:
            pyjama_item = pyjama_lookup[req_id]
            
            # Extract and accumulate requirements
            if "requirement" in pyjama_item:
                req_data = pyjama_item["requirement"]
                if req_data and isinstance(req_data, dict):
                    try:
                        from qaai.agents.shared.core import Requirement
                        req_obj = Requirement(**req_data) if isinstance(req_data, dict) else req_data
                        if req_obj not in requirements:
                            requirements.append(req_obj)
                    except Exception:
                        pass
            
            # Extract and accumulate test_cases
            if "test_cases" in pyjama_item:
                tcs = pyjama_item["test_cases"] or []
                for tc_data in tcs:
                    if tc_data:
                        try:
                            from qaai.agents.shared.core import TestCase
                            tc_obj = TestCase(**tc_data) if isinstance(tc_data, dict) else tc_data
                            if tc_obj not in test_cases:
                                test_cases.append(tc_obj)
                        except Exception:
                            pass
            
            # Extract and accumulate design_docs
            if "design_docs" in pyjama_item:
                dds = pyjama_item["design_docs"] or []
                for dd_data in dds:
                    if dd_data:
                        try:
                            from qaai.agents.shared.core import DesignDocument
                            dd_obj = DesignDocument(**dd_data) if isinstance(dd_data, dict) else dd_data
                            if dd_obj not in design_docs:
                                design_docs.append(dd_obj)
                        except Exception:
                            pass
            
            # Extract and accumulate system_requirements and user_needs nested within them
            if "system_requirements" in pyjama_item:
                sys_reqs = pyjama_item["system_requirements"] or []
                for sys_req_data in sys_reqs:
                    if sys_req_data:
                        try:
                            from qaai.agents.shared.core import Requirement
                            sys_req_obj = Requirement(**sys_req_data) if isinstance(sys_req_data, dict) else sys_req_data
                            if sys_req_obj not in system_requirements:
                                system_requirements.append(sys_req_obj)
                        except Exception:
                            pass
                        
                        # Extract user_needs nested within each system_requirement
                        if isinstance(sys_req_data, dict) and "user_needs" in sys_req_data:
                            u_needs = sys_req_data.get("user_needs") or []
                            for u_need_data in u_needs:
                                if u_need_data:
                                    try:
                                        from qaai.agents.shared.core import Requirement
                                        u_need_obj = Requirement(**u_need_data) if isinstance(u_need_data, dict) else u_need_data
                                        if u_need_obj not in user_needs:
                                            user_needs.append(u_need_obj)
                                    except Exception:
                                        pass

    # Create HazardTraceMatrix with collected items
    requirements_traceability = HazardTraceMatrix(
        requirements=requirements,
        test_cases=test_cases,
        design_docs=design_docs,
        system_requirements=system_requirements,
        user_needs=user_needs,
    )

    # Create HazardRowWithTraceMatrix by extending the excel_row with traceability
    output_row = HazardRowWithTraceMatrix(
        **excel_row.model_dump(),
        requirements_traceability=requirements_traceability,
    )

    return output_row
