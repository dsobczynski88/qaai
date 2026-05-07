from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Union, Sequence, Pattern, Any
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, Template
import json
import openpyxl
import autoqa
from autoqa.prj_logger import get_logs

def get_current_date_time():
    # Get the current date and time
    now = datetime.now()
    # Extract date, month, and time
    current_date = now.date()  # YYYY-MM-DD format
    current_month = now.month  # Numeric month (1-12)
    current_time = now.time()  # HH:MM:SS.microseconds format
    formatted_time = now.strftime("%Y-%m-%d-%H-%M-%S")
    return formatted_time  

def make_output_directory(fold_path):
    run_name = f"run-{get_current_date_time()}"
    output_directory = f"{fold_path}/{run_name}"
    Path(output_directory).mkdir(parents=True, exist_ok=True)
    return output_directory

def save_graph_png(graph, output_path: Union[str, Path]) -> None:
    """
    Render a compiled LangGraph runnable as a Mermaid PNG and save it to disk.

    Uses LangGraph's built-in draw_mermaid_png() which calls the Mermaid.ink
    public API — requires an internet connection. The PNG is a developer
    convenience artefact, so a render failure (offline, mermaid.ink outage)
    must NOT abort the overall pipeline run; we log a warning and continue.

    Args:
        graph: A compiled LangGraph runnable (result of StateGraph.compile()).
        output_path: Destination path for the PNG file. Parent directories are
                     created automatically.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        png_bytes = graph.get_graph().draw_mermaid_png()
    except Exception as e:
        print(f"warning: could not render {output_path.name} (mermaid.ink unreachable?): {e}")
        return
    output_path.write_bytes(png_bytes)
    print(f"Graph diagram saved to: {output_path}")


# Prompt Template Loading (Jinja2)
# Get the prompts directory path relative to this file
PROMPTS_DIR = Path(__file__).parent / "prompts"


def get_prompt_loader() -> Environment:
    """
    Create and return a Jinja2 Environment configured to load templates
    from the prompts directory.
    
    Returns:
        Environment: Configured Jinja2 environment
    """
    return Environment(
        loader=FileSystemLoader(str(PROMPTS_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True
    )


def load_prompt_template(template_name: str) -> Template:
    """
    Load a prompt template by name.
    
    Args:
        template_name: Name of the template file (e.g., 'decomposer.jinja2')
        
    Returns:
        Template: Loaded Jinja2 template
        
    Raises:
        FileNotFoundError: If template file doesn't exist
    """
    env = get_prompt_loader()
    return env.get_template(template_name)


def render_prompt(template_name: str, **kwargs: Any) -> str:
    """
    Load and render a prompt template with the given variables.
    
    Args:
        template_name: Name of the template file (e.g., 'decomposer.jinja2')
        **kwargs: Variables to pass to the template
        
    Returns:
        str: Rendered prompt text
        
    Example:
        >>> prompt = render_prompt('decomposer.jinja2', domain='medical devices')
    """
    template = load_prompt_template(template_name)
    return template.render(**kwargs)

def load_json(json_file: str) -> Dict[str, Any]:
    """Loads a JSON SBOM file"""
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"Failed to parse JSON from '{json_file}'.\n" \
            f"Details: {e}"
        )
    if not isinstance(data, dict):
        # Some tools wrap the document in a one-item list; handle that too
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        else:
            raise SystemExit("Unsupported JSON structure: expected an object at the top level.")

    return data

def _flatten(obj: Any, parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
    """Recursively flattens nested dict/list structures.

    Rules:
      - Dict keys are joined with `sep`.
      - Lists of atomic values are joined into a semicolon-separated string.
      - Lists containing dicts/lists are expanded with a numeric index: key[0].sub, key[1].sub, ...
    """
    items: Dict[str, Any] = {}

    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            items.update(_flatten(v, new_key, sep=sep))
    elif isinstance(obj, list):
        if not obj:
            # Preserve empty list columns to signal existence
            items[parent_key] = ""
        elif all(not isinstance(x, (dict, list)) for x in obj):
            # Atomic list -> join into a single cell
            items[parent_key] = "; ".join(map(lambda x: str(x) if x is not None else "", obj))
        else:
            # Heterogeneous or list of dicts/lists -> index each element
            for i, v in enumerate(obj):
                idx_key = f"{parent_key}[{i}]" if parent_key else f"[{i}]"
                items.update(_flatten(v, idx_key, sep=sep))
    else:
        items[parent_key] = obj

    return items

def _to_dataframe(things: List[Dict[str, Any]]) -> pd.DataFrame:
    """Converts a list of (possibly nested) dicts to a flattened DataFrame.

    Ensures the union of all keys across rows becomes the set of columns.
    """
    if not things:
        return pd.DataFrame()

    flattened_rows: List[Dict[str, Any]] = [_flatten(x) for x in things]
    # Build stable, sorted union of columns
    all_cols = sorted({k for row in flattened_rows for k in row.keys()})
    # Reindex rows to include all columns
    normalized_rows = [{col: row.get(col, None) for col in all_cols} for row in flattened_rows]
    df = pd.DataFrame(normalized_rows, columns=all_cols)
    return df

def json_to_dataframe(json_file, json_id="requirements", output_path="json_output.xlsx"):

    json_obj = load_json(json_file)
    json_items = json_obj.get(json_id, [])
    
    frames = {
        "Sheet1": _to_dataframe(json_items)
    }
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, df in frames.items():
            safe_name = sheet_name[:31]
            df.to_excel(writer, index=False, sheet_name=safe_name)
    return openpyxl.load_workbook(output_path)

def _extract_json_from_markdown(text: str) -> str:
    """Extract JSON from markdown code fences."""
    import re
    fence = re.search(r"```(?:json|jsonc)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    first_brace = text.find("{")
    first_bracket = text.find("[")
    starts = [i for i in (first_brace, first_bracket) if i != -1]
    if starts:
        return text[min(starts):].strip()
    return text.strip()