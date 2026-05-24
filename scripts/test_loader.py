#from autoqa.components.hazard_risk_reviewer.loader import parse_sha_excel

#result = parse_sha_excel(
#    file_path="tests/fixtures/external/software_hazard_analysis.xlsx",
#    sheet_name="SHA Table",
#    extract_gids_format= "REQ-PUMP-\\d+"
#)

#print(result)

from autoqa.components.shared.data_integration import transform_hazard_record_to_state

enhanced_rows = transform_hazard_record_to_state(
        excel_file_path="tests/fixtures/external/software_hazard_analysis.xlsx",
        pyjama_response_file_path="tests/fixtures/external/pyjama_response_unified.jsonl",
        output_jsonl_path="data/results.jsonl",
        sheet_name="SHA Table",
        extract_gids_format="REQ-PUMP-\\d+"
    )

print(enhanced_rows)