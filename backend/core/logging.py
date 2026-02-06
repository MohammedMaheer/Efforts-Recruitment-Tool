"""
Structured Logging Configuration
Production-ready logging with JSON formatting support
"""
import logging
import sys
from typing import Optional
from datetime import datetime
import json


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for production environments.
    Enables structured log analysis with tools like ELK, CloudWatch, etc.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        if hasattr(record, 'extra'):
            log_data.update(record.extra)
        
        return json.dumps(log_data)


class ColoredFormatter(logging.Formatter):
    """
    Colored log formatter for development environments.
    Makes logs easier to read during development.
    """
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        
        # Format timestamp
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        # Build formatted message
        formatted = f"{color}{timestamp} │ {record.levelname:8}{self.RESET} │ {record.name:20} │ {record.getMessage()}"
        
        # Add exception if present
        if record.exc_info:
            formatted += f"\n{self.formatException(record.exc_info)}"
        
        return formatted


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: Optional[str] = None
) -> None:
    """
    Configure application logging.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Use JSON formatting (for production)
        log_file: Optional file path for log output
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Choose formatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = ColoredFormatter()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JSONFormatter())  # Always JSON for files
        root_logger.addHandler(file_handler)
    
    # Quiet noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.
    
    Args:
        name: Logger name (typically __name__)
    
    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


# Performance logging context manager
class PerformanceLogger:
    """
    Context manager for logging operation performance.
    
    Usage:
        with PerformanceLogger(logger, "database_query"):
            result = db.execute(query)
    """
    
    def __init__(self, logger: logging.Logger, operation: str, threshold_ms: float = 100):
        self.logger = logger
        self.operation = operation
        self.threshold_ms = threshold_ms
        self.start_time: Optional[float] = None
    
    def __enter__(self):
        import time
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        import time
        if self.start_time:
            duration_ms = (time.perf_counter() - self.start_time) * 1000
            
            if duration_ms > self.threshold_ms:
                self.logger.warning(
                    f"Slow operation: {self.operation} took {duration_ms:.2f}ms"
                )
            else:
                self.logger.debug(
                    f"{self.operation} completed in {duration_ms:.2f}ms"
                )
        
        return False  # Don't suppress exceptions
