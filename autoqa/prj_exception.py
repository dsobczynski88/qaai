'''
Define custom exception handling
'''

import sys
import traceback
import time
import logging
from pathlib import Path

def exception_logger(loggername, return_on_error=None, reraise=True):
    """Log exceptions and optionally suppress them.
    
    This decorator logs exceptions with full context and either re-raises them
    or returns a default value. Use reraise=False only when silent failure is
    acceptable and well-documented.
    
    Args:
        loggername: Logger name for exception messages
        return_on_error: Value to return on exception (only if reraise=False)
        reraise: If True (default), re-raise the exception after logging
        
    Returns:
        Decorated function that logs exceptions
        
    Example:
        >>> @exception_logger("myapp.module", reraise=True)
        >>> def risky_operation():
        >>>     return 1 / 0  # Will log and re-raise ZeroDivisionError
        
        >>> @exception_logger("myapp.module", return_on_error=None, reraise=False)
        >>> def optional_operation():
        >>>     return 1 / 0  # Will log and return None
    """       
    def decorator(func):
        def wrapper(*args, **kwargs):          
            try:
                return func(*args, **kwargs)
            except Exception as e:
                ce = CustomException(e)
                logger = logging.getLogger(loggername)               
                logger.error(ce.error_message, exc_info=True)
                if reraise:
                    raise ce
                return return_on_error
        return wrapper
    return decorator

def parse_error_traceback(error_detail:sys):
    _,_,exc_tb=error_detail.exc_info()
    return exc_tb
    
def get_error_message(error, type, tb):    
    error_message=f"{type}:{error} occurred in {tb.name} (line {tb.lineno}) of {Path(tb.filename).name}"
    return error_message
 
class CustomException(Exception):
    
    def __init__(self, error):
        super().__init__(error)     
        # select element index 1 traceback frame (index 0 corresponds to decorator function level)
        self.tb = traceback.extract_tb(error.__traceback__)[1]
        self.error_type = type(error).__name__
        self.error_message = get_error_message(error, self.error_type, self.tb)

    def __str__(self):
        return self.error_message
     
