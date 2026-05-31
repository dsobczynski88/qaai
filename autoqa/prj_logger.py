import sys
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from autoqa.prj_exception import CustomException

# US Central Time zone (handles both CST and CDT automatically)
US_CENTRAL = ZoneInfo("America/Chicago")


def format_elapsed_time(seconds: float) -> str:
    """Format elapsed time as 'X minutes Y seconds'.
    
    Args:
        seconds: Elapsed time in seconds
        
    Returns:
        Human-readable string like '5 minutes 23 seconds' or '45 seconds'
    """
    minutes = int(seconds // 60)
    remaining_seconds = int(seconds % 60)
    if minutes > 0:
        return f"{minutes} minutes {remaining_seconds} seconds"
    else:
        return f"{remaining_seconds} seconds"


class CTFormatter(logging.Formatter):
    """Custom formatter that uses US Central Time for all log timestamps."""
    
    def formatTime(self, record, datefmt=None):
        """Override formatTime to use US Central Time instead of local/UTC."""
        dt = datetime.fromtimestamp(record.created, tz=US_CENTRAL)
        if datefmt:
            return dt.strftime(datefmt)
        else:
            # Default format: '2026-05-07 13:47:06,639' (CT)
            return dt.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]

def timing(loggername):
    def decorator(func):
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(loggername)
            start_time = time.perf_counter()
            logger.debug(f"Entering: {func.__name__}")
            output = func(*args, **kwargs)
            logger.debug(f"Exiting: {func.__name__}")
            end_time = time.perf_counter()
            elapsed_time = end_time - start_time
            logger.debug(f"{func.__name__} completed in {elapsed_time:.6f} seconds")
            return output
        return wrapper
    return decorator


class ProjectLogger:
    def __init__(self, name, log_file):
        self._name = name
        self._log_file = log_file
        self._logger = logging.getLogger(self._name)
        self._logger.setLevel(logging.DEBUG)

    @property
    def name(self):
        return self._name

    @property
    def log_file(self):
        return self._log_file

    @name.setter
    def name(self, new_name):
        self._name = new_name

    @log_file.setter
    def log_file(self, new_log_file):
        self._log_file = new_log_file

    def config(self):
        # create handlers
        file_handler = logging.FileHandler(self._log_file)
        console_handler = logging.StreamHandler(sys.stdout)

        # set logging levels
        file_handler.setLevel(logging.DEBUG)
        console_handler.setLevel(logging.DEBUG)

        # create formatters with CT timezone for file handler
        file_format = CTFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s')

        file_handler.setFormatter(file_format)
        console_handler.setFormatter(console_format)

        self._logger.addHandler(file_handler)
        self._logger.addHandler(console_handler)
        return self

    def get_logger(self):
        return self._logger