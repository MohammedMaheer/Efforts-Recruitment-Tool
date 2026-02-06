"""
Comprehensive Health Check System
Silicon Valley-grade system monitoring with:
- Component health checks
- Resource monitoring
- Liveness and readiness probes
- Dependency health tracking
- Performance metrics
"""
import asyncio
import logging
import os
import platform
import psutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Awaitable
import sys

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Health status levels"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    """Health status of a single component"""
    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: float = 0.0
    last_check: datetime = field(default_factory=datetime.now)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemResources:
    """System resource information"""
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_percent: float
    disk_used_gb: float
    disk_available_gb: float
    open_files: int
    threads: int
    
    @property
    def is_healthy(self) -> bool:
        return (
            self.cpu_percent < 90 and
            self.memory_percent < 90 and
            self.disk_percent < 90
        )


class HealthCheck:
    """
    Health check for a single component
    """
    
    def __init__(
        self,
        name: str,
        check_func: Callable[[], Awaitable[bool]],
        timeout: float = 5.0,
        critical: bool = False
    ):
        self.name = name
        self.check_func = check_func
        self.timeout = timeout
        self.critical = critical
        self._last_result: Optional[ComponentHealth] = None
    
    async def check(self) -> ComponentHealth:
        """Execute the health check"""
        start_time = time.time()
        
        try:
            async with asyncio.timeout(self.timeout):
                is_healthy = await self.check_func()
            
            latency = (time.time() - start_time) * 1000
            
            self._last_result = ComponentHealth(
                name=self.name,
                status=HealthStatus.HEALTHY if is_healthy else HealthStatus.UNHEALTHY,
                message="OK" if is_healthy else "Check failed",
                latency_ms=latency,
                last_check=datetime.now()
            )
            
        except asyncio.TimeoutError:
            latency = (time.time() - start_time) * 1000
            self._last_result = ComponentHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Timeout after {self.timeout}s",
                latency_ms=latency,
                last_check=datetime.now()
            )
            
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            self._last_result = ComponentHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Error: {str(e)[:100]}",
                latency_ms=latency,
                last_check=datetime.now()
            )
        
        return self._last_result
    
    @property
    def last_result(self) -> Optional[ComponentHealth]:
        return self._last_result


class HealthCheckManager:
    """
    Manages all health checks for the application
    """
    
    def __init__(self):
        self._checks: Dict[str, HealthCheck] = {}
        self._background_task: Optional[asyncio.Task] = None
        self._running = False
        self._check_interval = 30  # seconds
        self._startup_time = datetime.now()
    
    def register(
        self,
        name: str,
        check_func: Callable[[], Awaitable[bool]],
        timeout: float = 5.0,
        critical: bool = False
    ) -> None:
        """Register a health check"""
        self._checks[name] = HealthCheck(
            name=name,
            check_func=check_func,
            timeout=timeout,
            critical=critical
        )
        logger.debug(f"Registered health check: {name}")
    
    def unregister(self, name: str) -> None:
        """Unregister a health check"""
        if name in self._checks:
            del self._checks[name]
    
    async def start(self, check_interval: int = 30) -> None:
        """Start background health checking"""
        if self._running:
            return
        
        self._running = True
        self._check_interval = check_interval
        self._background_task = asyncio.create_task(self._check_loop())
        logger.info("✅ Health check manager started")
    
    async def stop(self) -> None:
        """Stop background health checking"""
        self._running = False
        
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🔌 Health check manager stopped")
    
    async def _check_loop(self) -> None:
        """Background loop to periodically run health checks"""
        while self._running:
            try:
                await self.check_all()
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
                await asyncio.sleep(5)
    
    async def check_all(self) -> Dict[str, ComponentHealth]:
        """Run all health checks"""
        results = {}
        
        # Run checks concurrently
        tasks = {
            name: check.check()
            for name, check in self._checks.items()
        }
        
        completed = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        for name, result in zip(tasks.keys(), completed):
            if isinstance(result, Exception):
                results[name] = ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check failed: {str(result)[:100]}"
                )
            else:
                results[name] = result
        
        return results
    
    async def check_one(self, name: str) -> Optional[ComponentHealth]:
        """Run a specific health check"""
        if name not in self._checks:
            return None
        return await self._checks[name].check()
    
    def get_last_results(self) -> Dict[str, ComponentHealth]:
        """Get last cached results"""
        return {
            name: check.last_result
            for name, check in self._checks.items()
            if check.last_result
        }
    
    def get_system_resources(self) -> SystemResources:
        """Get current system resource usage"""
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            process = psutil.Process()
            
            return SystemResources(
                cpu_percent=cpu,
                memory_percent=memory.percent,
                memory_used_mb=memory.used / (1024 * 1024),
                memory_available_mb=memory.available / (1024 * 1024),
                disk_percent=disk.percent,
                disk_used_gb=disk.used / (1024 ** 3),
                disk_available_gb=disk.free / (1024 ** 3),
                open_files=len(process.open_files()) if hasattr(process, 'open_files') else 0,
                threads=process.num_threads()
            )
        except Exception as e:
            logger.error(f"Error getting system resources: {e}")
            return SystemResources(
                cpu_percent=0, memory_percent=0, memory_used_mb=0,
                memory_available_mb=0, disk_percent=0, disk_used_gb=0,
                disk_available_gb=0, open_files=0, threads=0
            )
    
    async def get_overall_status(self) -> Dict[str, Any]:
        """Get overall system health status"""
        results = await self.check_all()
        resources = self.get_system_resources()
        
        # Determine overall status
        critical_healthy = all(
            result.status == HealthStatus.HEALTHY
            for name, result in results.items()
            if self._checks[name].critical
        )
        
        all_healthy = all(
            result.status == HealthStatus.HEALTHY
            for result in results.values()
        )
        
        if critical_healthy and all_healthy:
            overall_status = HealthStatus.HEALTHY
        elif critical_healthy:
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.UNHEALTHY
        
        uptime = datetime.now() - self._startup_time
        
        return {
            'status': overall_status.value,
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': uptime.total_seconds(),
            'uptime_human': str(uptime).split('.')[0],
            'components': {
                name: {
                    'status': result.status.value,
                    'message': result.message,
                    'latency_ms': round(result.latency_ms, 2),
                    'last_check': result.last_check.isoformat(),
                    'critical': self._checks[name].critical
                }
                for name, result in results.items()
            },
            'system': {
                'cpu_percent': round(resources.cpu_percent, 1),
                'memory_percent': round(resources.memory_percent, 1),
                'memory_used_mb': round(resources.memory_used_mb, 1),
                'memory_available_mb': round(resources.memory_available_mb, 1),
                'disk_percent': round(resources.disk_percent, 1),
                'disk_available_gb': round(resources.disk_available_gb, 1),
                'threads': resources.threads,
                'is_healthy': resources.is_healthy
            },
            'environment': {
                'python_version': sys.version.split()[0],
                'platform': platform.system(),
                'hostname': platform.node()
            }
        }
    
    async def liveness(self) -> Dict[str, Any]:
        """
        Kubernetes liveness probe
        Returns healthy if the application is running
        """
        return {
            'status': 'alive',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': (datetime.now() - self._startup_time).total_seconds()
        }
    
    async def readiness(self) -> Dict[str, Any]:
        """
        Kubernetes readiness probe
        Returns ready if the application can handle requests
        """
        # Check critical components only
        results = {}
        for name, check in self._checks.items():
            if check.critical:
                results[name] = await check.check()
        
        all_ready = all(
            result.status == HealthStatus.HEALTHY
            for result in results.values()
        )
        
        return {
            'status': 'ready' if all_ready else 'not_ready',
            'timestamp': datetime.now().isoformat(),
            'components': {
                name: result.status.value
                for name, result in results.items()
            }
        }


# Pre-built health check functions
async def check_database(db_service) -> bool:
    """Health check for database"""
    try:
        # Simple query to test connectivity
        result = await asyncio.to_thread(db_service.get_total_candidates)
        return isinstance(result, int)
    except Exception:
        return False


async def check_ai_service(ai_service) -> bool:
    """Health check for AI service"""
    try:
        # Check if model is loaded
        return ai_service.sentence_model is not None
    except Exception:
        return False


async def check_disk_space(min_gb: float = 1.0) -> bool:
    """Health check for disk space"""
    try:
        disk = psutil.disk_usage('/')
        available_gb = disk.free / (1024 ** 3)
        return available_gb >= min_gb
    except Exception:
        return False


async def check_memory(max_percent: float = 90.0) -> bool:
    """Health check for memory usage"""
    try:
        memory = psutil.virtual_memory()
        return memory.percent < max_percent
    except Exception:
        return False


# Global health check manager
_health_manager: Optional[HealthCheckManager] = None


def get_health_manager() -> HealthCheckManager:
    """Get or create the global health check manager"""
    global _health_manager
    
    if _health_manager is None:
        _health_manager = HealthCheckManager()
    
    return _health_manager


async def setup_health_checks(
    db_service=None,
    ai_service=None
) -> HealthCheckManager:
    """
    Setup standard health checks for the application
    """
    manager = get_health_manager()
    
    # Database health check
    if db_service:
        manager.register(
            name="database",
            check_func=lambda: check_database(db_service),
            timeout=5.0,
            critical=True
        )
    
    # AI service health check
    if ai_service:
        manager.register(
            name="ai_service",
            check_func=lambda: check_ai_service(ai_service),
            timeout=3.0,
            critical=False
        )
    
    # System resource checks
    manager.register(
        name="disk_space",
        check_func=lambda: check_disk_space(1.0),
        timeout=2.0,
        critical=False
    )
    
    manager.register(
        name="memory",
        check_func=lambda: check_memory(90.0),
        timeout=2.0,
        critical=False
    )
    
    # Start background checking
    await manager.start(check_interval=30)
    
    return manager
