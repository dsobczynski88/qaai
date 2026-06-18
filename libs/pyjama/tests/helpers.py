"""Shared helper functions for tests."""
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime


def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    """Load data from a JSONL file.
    
    Args:
        filepath: Path to the JSONL file
        
    Returns:
        List of dictionaries, one per line
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        json.JSONDecodeError: If a line contains invalid JSON
    """
    data = []
    path = Path(filepath)
    
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    
    with open(path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise json.JSONDecodeError(
                    f"Invalid JSON on line {line_num}: {e.msg}",
                    e.doc,
                    e.pos
                )
    
    return data


def save_jsonl(data: List[Dict[str, Any]], filepath: str) -> None:
    """Save data to a JSONL file.
    
    Args:
        data: List of dictionaries to save
        filepath: Path where the file should be saved
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(path, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')