"""Privacy-conscious CSV cleanup for the Spreadsheet Rescue service."""

from .engine import RescueError, RescueResult, rescue_csv

__all__ = ["RescueError", "RescueResult", "rescue_csv"]
__version__ = "1.0.0"
