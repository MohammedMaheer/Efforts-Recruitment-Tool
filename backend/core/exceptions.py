"""
Custom Exception Classes with Structured Error Handling
Enables consistent error responses across the API
"""
from typing import Any, Dict, Optional
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)


class AppException(Exception):
    """
    Base application exception.
    All custom exceptions should inherit from this.
    """
    
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": True,
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ValidationError(AppException):
    """Raised when input validation fails"""
    
    def __init__(self, message: str, field: Optional[str] = None, details: Optional[Dict] = None):
        super().__init__(
            message=message,
            status_code=400,
            error_code="VALIDATION_ERROR",
            details={"field": field, **(details or {})}
        )


class NotFoundError(AppException):
    """Raised when a resource is not found"""
    
    def __init__(self, resource: str, identifier: Optional[str] = None):
        super().__init__(
            message=f"{resource} not found" + (f": {identifier}" if identifier else ""),
            status_code=404,
            error_code="NOT_FOUND",
            details={"resource": resource, "identifier": identifier}
        )


class DatabaseError(AppException):
    """Raised when a database operation fails"""
    
    def __init__(self, message: str, operation: Optional[str] = None):
        super().__init__(
            message=f"Database error: {message}",
            status_code=500,
            error_code="DATABASE_ERROR",
            details={"operation": operation}
        )


class AIServiceError(AppException):
    """Raised when AI service fails"""
    
    def __init__(self, message: str, service: str = "local", timeout: bool = False):
        super().__init__(
            message=f"AI service error: {message}",
            status_code=503,
            error_code="AI_SERVICE_ERROR",
            details={"service": service, "timeout": timeout}
        )


class RateLimitError(AppException):
    """Raised when rate limit is exceeded"""
    
    def __init__(self, retry_after: int = 60):
        super().__init__(
            message="Rate limit exceeded. Please try again later.",
            status_code=429,
            error_code="RATE_LIMIT_EXCEEDED",
            details={"retry_after_seconds": retry_after}
        )


class AuthenticationError(AppException):
    """Raised when authentication fails"""
    
    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            message=message,
            status_code=401,
            error_code="AUTHENTICATION_ERROR"
        )


class FileProcessingError(AppException):
    """Raised when file processing fails"""
    
    def __init__(self, message: str, filename: Optional[str] = None):
        super().__init__(
            message=f"File processing error: {message}",
            status_code=422,
            error_code="FILE_PROCESSING_ERROR",
            details={"filename": filename}
        )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """
    Global exception handler for AppException and subclasses.
    Provides consistent error response format.
    """
    logger.warning(
        f"AppException: {exc.error_code} - {exc.message}",
        extra={
            "status_code": exc.status_code,
            "path": request.url.path,
            "details": exc.details
        }
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict()
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Global handler for unhandled exceptions.
    Logs full traceback and returns sanitized response.
    """
    logger.exception(
        f"Unhandled exception: {str(exc)}",
        extra={"path": request.url.path}
    )
    
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "error_code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred. Please try again.",
            "details": {}
        }
    )
