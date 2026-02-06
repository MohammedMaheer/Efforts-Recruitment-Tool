# Core module initialization
# Silicon Valley-grade backend infrastructure

from .config import settings
from .exceptions import (
    AppException,
    ValidationError,
    NotFoundError,
    DatabaseError,
    AIServiceError,
    RateLimitError,
)
from .logging import get_logger

# Performance and optimization modules
from .cache import CacheService, MemoryCache, get_cache_service, cached
from .database import AsyncDatabaseManager, AsyncConnectionPool, get_db_manager
from .middleware import (
    TimingMiddleware,
    CompressionMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    CacheControlMiddleware,
    setup_middleware,
    metrics_collector,
)
from .tasks import (
    BackgroundTaskManager,
    Task,
    TaskStatus,
    TaskPriority,
    background_task,
    get_task_manager,
)
from .health import (
    HealthCheckManager,
    HealthStatus,
    get_health_manager,
    setup_health_checks,
)
from .ai_optimizer import OptimizedAIService, create_optimized_ai_service

__all__ = [
    # Config
    'settings',
    
    # Exceptions
    'AppException',
    'ValidationError', 
    'NotFoundError',
    'DatabaseError',
    'AIServiceError',
    'RateLimitError',
    
    # Logging
    'get_logger',
    
    # Cache
    'CacheService',
    'MemoryCache',
    'get_cache_service',
    'cached',
    
    # Database
    'AsyncDatabaseManager',
    'AsyncConnectionPool',
    'get_db_manager',
    
    # Middleware
    'TimingMiddleware',
    'CompressionMiddleware',
    'RateLimitMiddleware',
    'SecurityHeadersMiddleware',
    'CacheControlMiddleware',
    'setup_middleware',
    'metrics_collector',
    
    # Background tasks
    'BackgroundTaskManager',
    'Task',
    'TaskStatus',
    'TaskPriority',
    'background_task',
    'get_task_manager',
    
    # Health checks
    'HealthCheckManager',
    'HealthStatus',
    'get_health_manager',
    'setup_health_checks',
    
    # AI optimization
    'OptimizedAIService',
    'create_optimized_ai_service',
]
