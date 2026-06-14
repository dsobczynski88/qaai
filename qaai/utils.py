from pathlib import Path
from typing import Any, Union
from jinja2 import Environment, FileSystemLoader, Template
import qaai

def render_graph_png(graph) -> Union[bytes, None]:
    """Render a compiled LangGraph runnable to Mermaid PNG bytes (or None).

    Uses LangGraph's built-in draw_mermaid_png() which calls the Mermaid.ink
    public API — requires an internet connection. The PNG is a developer
    convenience artefact, so a render failure (offline, mermaid.ink outage) must
    NOT abort the run; we warn and return None. Render once at graph build time,
    then write the cached bytes into each per-run folder via write_graph_png_bytes.

    Args:
        graph: A compiled LangGraph runnable (result of StateGraph.compile()).
    """
    try:
        return graph.get_graph().draw_mermaid_png()
    except Exception as e:
        print(f"warning: could not render graph png (mermaid.ink unreachable?): {e}")
        return None


def write_graph_png_bytes(png_bytes: Union[bytes, None], output_path: Union[str, Path]) -> None:
    """Write previously-rendered PNG bytes to disk, creating parent dirs.

    No-op when png_bytes is None (render failed), so a missing diagram never
    aborts a run.
    """
    if not png_bytes:
        return
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
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

