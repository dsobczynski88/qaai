from pathlib import Path
from datetime import datetime
import shutil
import re
import ast
import inspect
import yaml

def make_directory(root_dir: str, dir_name: str, use_existing=True):
    # Specify a new directory name relative to `data_path`
    dir_path = Path(f"{root_dir}/{dir_name}")
    # Create the directory
    folder_num = 0
    while True:
        try:
            dir_path.mkdir()
            print(f"Directory '{dir_path}' created successfully.")
            break
        except FileExistsError:
            print(f"Directory '{dir_path}' already exists.")
            if not use_existing:
                dir_name = f"{dir_name}_{folder_num}"
                dir_path = Path(f"{root_dir}/{dir_name}")
                folder_num += 1
            else:
                break
        except PermissionError:
            print(f"Permission denied: Unable to create '{dir_path}'.")
            break
        except Exception as e:
            print(f"An error occurred: {e}")
            break
    return dir_name

def convert_string_to_float(x):
    try:
        x = float(x)
    except ValueError:
        x = float(0)
    return x


def found_empty_args(args, logger):
    arg_items = vars(args).items()
    for arg_name, arg_val in arg_items:
        try:
            assert arg_val is not None
        except AssertionError:
            if logger is not None:
                logger.error(f"The value for argument: {arg_name} cannot be of NoneType")
            raise

def apply_regex(pat, s, default_value):
    matches = re.findall(pat, s)
    if len(matches) >= 1:
        return matches
    else:
        return default_value

def recast_str(_str:str, na_value=[]):
    """This function takes in a str and default value for errors or NaNs. The built-in
    python function eval() is applied on the input string in effort to cast the string
    to some expected data type (e.g., list, dict). This is particularly useful as exporting
    pandas dataframes to excel may result in loss of data typing and this is a way to 
    recover this infomation. In the event there is a type or syntax error with the eval()
    function, the na_value is returned.

    """  
    if type(_str) == list:
        return _str
    elif type(_str) == dict:
        return _str
    elif (type(_str) == float) or (str(_str) == 'nan'):
        return na_value
    else:
        try:
            casted = ast.literal_eval(_str)
        except SyntaxError:
            print(f'The following string was unable to be casted using eval()')
            print(f'The value will be converted to data type: {type(na_value)}')
            return na_value
        except TypeError:
            print(f'The input data type: {type((_str)).__name__} cannot be evaluated')
            return na_value
        except NameError:
            return na_value
        except ValueError:
            return _str
        else:
            return casted
        
def map_A_to_B(list_of_A:list, mapdict_AB:dict) -> list:
    """This function takes in a list (call as A) and a 
    dictionary who keys include elements of the list A. Using
    this dictionary and the input list A, an output list is generaed
    where the original elements of the list A are mapped to the 
    values of the dictionary provided for each key.

    Args:
        list_of_A (list): an input list
        mapdict_AB (dict): a dictionary with keys corresponding to the input list elements
    """
    return [*map(mapdict_AB.get, list_of_A)]

def get_var_name(var):
    current_frame = inspect.currentframe()
    caller_frame = inspect.getouterframes(current_frame)[1]
    local_vars = caller_frame.frame.f_locals
    for name, value in local_vars.items():
        if value is var:
            return name

def get_current_date_time():
    now = datetime.now()
    formatted_time = now.strftime("%Y-%m-%d-%H-%M-%S")
    return formatted_time

def make_output_directory(fold_path):
    run_name = f"run-{get_current_date_time()}"
    output_directory = f"{fold_path}/{run_name}"
    Path(output_directory).mkdir(parents=True, exist_ok=True)
    return output_directory

def yaml_loader(config_file='config.yml'):
    try:
        with open(config_file, 'r') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        return None
        
def yaml_writer(yaml_data, output_file='config.yml', sort_keys=False, indent=2):
    try:
        with open(output_file, 'w') as f:
            yaml.dump(yaml_data, f, sort_keys=sort_keys, indent=2)
    except FileNotFoundError:
        return None