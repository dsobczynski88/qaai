from pathlib import Path
from typing import Any, Union
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, Template
import autoqa

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

