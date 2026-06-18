'''
Define custom exception handling
'''
import sys
import traceback
import time
import logging
from functools import wraps
from pathlib import Path

def parse_error_traceback(error_detail:sys):
    _,_,exc_tb=error_detail.exc_info()
    return exc_tb
    
def get_error_message(error, type, tb):    
    error_message=f"{type}:{error} occurred in {tb.name} (line {tb.lineno}) of {Path(tb.filename).name}"
    return error_message
 
class CustomException(Exception):
    
    def __init__(self, error, level):
        super().__init__(error)     
        # select element index 1 traceback frame (index 0 corresponds to decorator function level)
        self.tb = traceback.extract_tb(error.__traceback__)[level]
        self.error_type = type(error).__name__
        self.error_message = get_error_message(error, self.error_type, self.tb)

    def __str__(self):
        return self.error_message
   
'''   
def exception_logger(loggername):        
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                ce = CustomException(e)
                logger = logging.getLogger(loggername)                
                logger.debug(ce.error_message)
                #raise ce
                return None
        return wrapper
    return decorator
'''
  
def exception_logger(loggername, level=0):        
    def decorator(func):
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(loggername)
            start_time = time.perf_counter()
            logger.debug(f"Entering: {func.__name__}")
            try:
                output = func(*args, **kwargs)
            except Exception as e:
                ce = CustomException(e, level)
                logger = logging.getLogger(loggername)                
                logger.debug(ce.error_message)
                output = None
            finally:
                logger.debug(f"Exiting: {func.__name__}")
                end_time = time.perf_counter()
                elapsed_time = end_time - start_time
                logger.debug(f"{func.__name__} completed in {elapsed_time:.6f} seconds")
                return output
        return wrapper
    return decorator


def async_exception_logger(loggername, level=0):
    """Async-compatible version of exception_logger for coroutine functions."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            logger = logging.getLogger(loggername)
            start_time = time.perf_counter()
            logger.debug(f"Entering: {func.__name__}")
            output = None
            try:
                output = await func(*args, **kwargs)
            except Exception as e:
                ce = CustomException(e, level)
                logger.debug(ce.error_message)
            finally:
                logger.debug(f"Exiting: {func.__name__}")
                end_time = time.perf_counter()
                elapsed_time = end_time - start_time
                logger.debug(f"{func.__name__} completed in {elapsed_time:.6f} seconds")
                return output
        return wrapper
    return decorator