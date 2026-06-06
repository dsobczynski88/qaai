#!/usr/bin/env python3
"""Migrate prompts from flat structure to versioned registry - Version 2."""
import shutil
from pathlib import Path
from datetime import date
import yaml

PROMPTS_DIR = Path("/home/jovyan/shared/renalpa/autoqa-5-may/autoqa/prompts")

def create_meta_yaml(role: str, version: str, component: str, parent_version: str = None) -> dict:
    """Create meta.yaml content for a prompt."""
    output_models = {
        "decomposer": "DecomposedRequirement",
        "summarizer": "SummarizedTestCaseList",
        "coverage_evaluator": "EvaluatedSpec",
        "synthesizer": "SynthesizedAssessment",
        "single_test_aggregator": "TestCaseAssessment",
        "single_test_coverage_eval": "CoverageAnalysis",
        "single_test_logical_steps": "LogicalStepsAnalysis",
        "single_test_prereqs": "PrerequisitesAnalysis",
        "hazard_final_assessor": "HazardAssessment",
        "hazard_h1": "H1Finding",
        "hazard_h2": "H2Finding",
        "hazard_h3": "H3Finding",
        "hazard_h4": "H4Finding",
        "hazard_h5": "H5Finding",
        "hazard_h6": "H6Finding",
        "hazard_h7": "H7Finding",
        "shared_evaluator_conventions": None,
        "design_summarizer": "DesignSummary",
    }
    
    rubrics = {
        "synthesizer": ["M1", "M2", "M3", "M4", "M5"],
        "hazard_final_assessor": ["H1", "H2", "H3", "H4", "H5", "H6", "H7"],
    }
    
    meta = {
        "role": role,
        "version": version,
        "component": component,
        "authored": str(date.today()),
        "status": "published",
        "parent_version": parent_version,
        "author": "autoqa-team",
        "required_template_vars": [],
        "output_pydantic_model": output_models.get(role),
        "target_models": ["gpt-4o-mini", "gpt-4o"],
        "changelog": "Initial migration from flat structure.",
    }
    
    if role in rubrics:
        meta["rubric"] = rubrics[role]
    
    return meta

# Mapping: (source_file, role, version, component, parent_version)
MIGRATIONS = [
    # test_suite_reviewer
    ("decomposer-v5.jinja2", "decomposer", "v5.0.0", "common", None),
    ("summarizer-v4.jinja2", "summarizer", "v4.0.0", "test_suite_reviewer", None),
    ("coverage_evaluator-v7.jinja2", "coverage_evaluator", "v7.0.0", "test_suite_reviewer", "v6.0.0"),
    ("synthesizer-v7.jinja2", "synthesizer", "v7.0.0", "test_suite_reviewer", "v6.0.0"),
    
    # test_case_reviewer
    ("single-test-aggregator-v4.jinja2", "single_test_aggregator", "v4.0.0", "test_case_reviewer", "v3.0.0"),
    ("single-test-coverage-eval-v3.jinja2", "single_test_coverage_eval", "v3.0.0", "test_case_reviewer", "v2.0.0"),
    ("single-test-logical-steps-v3.jinja2", "single_test_logical_steps", "v3.0.0", "test_case_reviewer", "v2.0.0"),
    ("single-test-prereqs-v3.jinja2", "single_test_prereqs", "v3.0.0", "test_case_reviewer", "v2.0.0"),
    
    # hazard_risk_reviewer
    ("H1_hazard_record_completeness_and_semantic_integrity.jinja2", "hazard_h1", "v1.0.0", "hazard_risk_reviewer", None),
    ("H2_software_contribution_and_cause_coverage.jinja2", "hazard_h2", "v1.0.0", "hazard_risk_reviewer", None),
    ("H3_pre_mitigation_risk_and_exploitability_characterization.jinja2", "hazard_h3", "v1.0.0", "hazard_risk_reviewer", None),
    ("H4_risk_control_identification_allocation_and_coverage.jinja2", "hazard_h4", "v1.0.0", "hazard_risk_reviewer", None),
    ("H5_verification_depth_and_hazard_path_effectiveness.jinja2", "hazard_h5", "v1.0.0", "hazard_risk_reviewer", None),
    ("H6_residual_risk_closure_and_acceptability_decision.jinja2", "hazard_h6", "v1.0.0", "hazard_risk_reviewer", None),
    ("H7_hsha_update_and_newly_identified_hazard_capture.jinja2", "hazard_h7", "v1.0.0", "hazard_risk_reviewer", None),
    ("hazard_final_assessor-v1.jinja2", "hazard_final_assessor", "v1.0.0", "hazard_risk_reviewer", None),
    ("shared_evaluator_conventions.jinja2", "shared_evaluator_conventions", "v1.0.0", "hazard_risk_reviewer", None),
]

# Files to move to misc/
MISC_FILES = [
    "add_labels_to_synthetic_input_generator.jinja2",
    "coverage_evaluator-v2.jinja2",
    "coverage_evaluator-v3.jinja2",
    "coverage_evaluator-v3-user.jinja2",
    "coverage_evaluator-v4.jinja2",
    "coverage_evaluator-v5.jinja2",
    "coverage_evaluator-v6.jinja2",
    "coverage_evaluator.jinja2",
    "rewrite-coverage-instructions.jinja2",
    "single-test-aggregator.jinja2",
    "single-test-aggregator-v2.jinja2",
    "single-test-aggregator-v3.jinja2",
    "single-test-coverage-eval.jinja2",
    "single-test-coverage-eval-v2.jinja2",
    "single-test-logical-steps.jinja2",
    "single-test-logical-steps-v2.jinja2",
    "single-test-prereqs.jinja2",
    "single-test-prereqs-v2.jinja2",
    "summarizer.jinja2",
    "summarizer-v2.jinja2",
    "summarizer-v3.jinja2",
    "synthesizer-v2.jinja2",
    "synthesizer-v3.jinja2",
    "synthesizer-v4.jinja2",
    "synthesizer-v5.jinja2",
    "synthesizer-v6.jinja2",
    "synthesizer_assessment.jinja2",
    "synthetic_data_decomposer.jinja2",
    "synthetic_data_generator.jinja2",
    "synthetic_input_generator_boundary_gap.jinja2",
    "synthetic_input_generator_functional_gap.jinja2",
    "synthetic_input_generator_negative_gap.jinja2",
    "synthetic_input_generator_no_expected_gaps.jinja2",
    "test_generator.jinja2",
]

def main():
    print("Starting prompt migration v2...")
    
    # Migrate mapped prompts
    for source_file, role, version, component, parent_version in MIGRATIONS:
        source = PROMPTS_DIR / source_file
        if not source.exists():
            print(f"WARNING: {source_file} not found, skipping")
            continue
        
        # Destination paths
        dest_dir = PROMPTS_DIR / role / version
        dest_template = dest_dir / "template.jinja2"
        dest_meta = dest_dir / "meta.yaml"
        
        # Copy template
        shutil.copy2(source, dest_template)
        print(f"✓ Copied {source_file} -> {role}/{version}/template.jinja2")
        
        # Create meta.yaml
        meta = create_meta_yaml(role, version, component, parent_version)
        
        with dest_meta.open("w") as f:
            yaml.safe_dump(meta, f, sort_keys=False, default_flow_style=False)
        print(f"✓ Created {role}/{version}/meta.yaml")
    
    # Create design_summarizer placeholder
    design_dir = PROMPTS_DIR / "design_summarizer" / "v1.0.0"
    placeholder_template = design_dir / "template.jinja2"
    placeholder_content = """### Role
# Placeholder for future design document summarizer
# TODO: Implement design summarizer prompt

### Purpose
This prompt will be used to summarize design documents for test coverage analysis.

### Status
Draft - Not yet implemented
"""
    placeholder_template.write_text(placeholder_content)

    meta = create_meta_yaml("design_summarizer", "v1.0.0", "test_suite_reviewer")
    meta["status"] = "draft"
    meta["changelog"] = "Placeholder for future implementation."
    
    meta_path = design_dir / "meta.yaml"
    with meta_path.open("w") as f:
        yaml.safe_dump(meta, f, sort_keys=False, default_flow_style=False)
    print(f"✓ Created design_summarizer/v1.0.0/ (placeholder)")
    
    # Move misc files
    moved_count = 0
    for filename in MISC_FILES:
        source = PROMPTS_DIR / filename
        if source.exists() and source.is_file():
            dest = PROMPTS_DIR / "misc" / filename
            shutil.move(str(source), str(dest))
            print(f"✓ Moved {filename} -> misc/")
            moved_count += 1
    
    print(f"\n{'='*60}")
    print(f"Migration complete!")
    print(f"  Prompts migrated: {len(MIGRATIONS)}")
    print(f"  Files moved to misc/: {moved_count}")
    print(f"  Placeholder created: design_summarizer/v1.0.0")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()