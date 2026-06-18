from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Tuple
import pandas as pd
import flatdict
from src.utils import gen_utils
        
def flatten_dict_columns(df: pd.DataFrame, dict_cols: List[str]) -> pd.DataFrame:
    """
    Natively unpacks dictionary-containing columns into separate columns.
    Replaces both 'flatten' and 'flatten_df_series_dict'.
    """
    for col in dict_cols:
        # 1. Use Pandas native normalization to expand the dicts
        normalized_df = pd.json_normalize(df[col]).add_prefix(f"{col}_")
        
        # 2. Drop the original dict column and concatenate the new expanded columns
        df = pd.concat([df.drop(columns=[col]), normalized_df], axis=1)
        
    return df

def dict_list_to_df(list_of_dicts: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Converts a list of dictionaries into a DataFrame. 
    
    Automatically aligns keys as columns. Missing keys in individual 
    dictionaries are inherently populated with NaN.
    
    Args:
        list_of_dicts: A list of dictionaries to convert.
        
    Returns:
        pd.DataFrame: The resulting DataFrame with a clean RangeIndex.
    """
    return pd.DataFrame(list_of_dicts)

def get_types_dict(df: pd.DataFrame) -> Dict[str, str]:
    """
    Evaluates column data types efficiently, skipping element-wise 
    checks for homogeneous numeric types. Flags mixed types or lists.
    """
    types_dict = {}
    
    for col in df.columns:
        # 1. Bypass element-level checks for native types (int, float, bool)
        if df[col].dtype != 'object':
            first_idx = df[col].first_valid_index()
            # Fallback to NoneType if the entire column is empty
            type_name = type(df.at[first_idx, col]).__name__ if first_idx is not None else 'NoneType'
            types_dict[col] = type_name
            continue
            
        # 2. For 'object' columns, use optimized map() and drop nulls
        unique_types = df[col].dropna().map(type).unique()
        
        if len(unique_types) == 1:
            type_name = unique_types[0].__name__
            types_dict[col] = type_name
            
            if type_name == 'list':
                print(f"Column '{col}' is of data type: {type_name}")
                
        elif len(unique_types) > 1:
            type_names = [t.__name__ for t in unique_types]
            print(f"Column '{col}' has multiple data types: {type_names}")
            
    return types_dict

def replace_null(df: pd.DataFrame, colname: str, replace_with: str) -> pd.DataFrame:
    """Replaces null values in a specific column natively."""
    df[colname] = df[colname].fillna(replace_with)
    return df
    
    
def mk_dict_from_df(df: pd.DataFrame, cols_to_keep: list) -> dict:
    """Converts two specific DataFrame columns into a dictionary key-value pair."""
    return (
        df.drop_duplicates(subset=cols_to_keep)
        .set_index(cols_to_keep[0])[cols_to_keep[1]]
        .to_dict()
    )

def handle_duplicate_df_col_names(df: pd.DataFrame) -> pd.DataFrame:
    """Renames duplicate columns by appending a sequential suffix."""
    seen = {}
    new_cols = []
    
    for col in df.columns:
        if col in seen:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            new_cols.append(col)
            
    df.columns = new_cols
    return df

def to_excel(df, output_folder, df_name=False, _id=False, output_file=False):
    if df_name and _id:
        df.to_excel(f'{output_folder}/{df_name}_{_id}.xlsx')
    elif df_name and not _id:
        df.to_excel(f'{output_folder}/{df_name}.xlsx')
    elif output_file:
        df.to_excel(f'{output_folder}/{output_file}')
        
def combine_dfs(directory: Path, project_number: str, file_pat: str = "reviewed_baseline_df_*", keep_cols: list = ['documentKey','signed','lastActivityDate']) -> pd.DataFrame:
    """Reads and concatenates specific columns from multiple Excel files."""
    
    # 1. Use pathlib's built in glob for cleaner searching
    matching_files = list(directory.glob(file_pat))
    
    # 2. Use 'usecols' to drastically reduce memory usage during the read phase
    dfs = [
        pd.read_excel(file, usecols=keep_cols) 
        for file in matching_files
    ]
    
    if not dfs:
        return pd.DataFrame() # Handle empty directory edge-case
        
    combined_df = pd.concat(dfs, ignore_index=True)
    
    # 3. Use pathlib for safe path joining, avoiding string formatting errors
    output_path = directory / f"combined_df_{project_number}.xlsx"
    combined_df.to_excel(output_path, index=False)
    
    return combined_df