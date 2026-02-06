"""
High-Performance FastAPI Middleware Stack
Silicon Valley-grade middleware with:
- Request/Response timing
- Response compression (gzip/brotli)
- Rate limiting
- Request ID tracking
- Performance metrics
- Security headers
"""
import asyncio
import gzip
import hashlib
import time
import uuid
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple, Any
from functools import wraps
import json

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


logger = logging.getLogger(__name__)


@dataclass
class RequestMetrics:
    """Metrics for a single request"""
    request_id: str
    method: str
    path: str
    status_code: int
    response_time_ms: float
    request_size: int
    response_size: int
    timestamp: datetime
    client_ip: str
    user_agent: str


@dataclass
class EndpointStats:
    """Statistics for an endpoint"""
    total_requests: int = 0
    total_errors: int = 0
    total_response_time_ms: float = 0.0
    min_response_time_ms: float = float('inf')
    max_response_time_ms: float = 0.0
    response_times: List[float] = field(default_factory=list)
    status_codes: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    
    @property
    def avg_response_time_ms(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_response_time_ms / self.total_requests
    
    @property
    def p95_response_time_ms(self) -> float:
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]
    
    @property
    def p99_response_time_ms(self) -> float:
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]
    
    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests


class MetricsCollector:
    """
    Centralized metrics collection for the application
    Thread-safe and performant
    """
    
    def __init__(self, max_history: int = 10000):
        self.max_history = max_history
        self._endpoint_stats: Dict[str, EndpointStats] = defaultdict(EndpointStats)
        self._recent_requests: List[RequestMetrics] = []
        self._lock = asyncio.Lock()
        self._start_time = datetime.now()
    
    async def record_request(self, metrics: RequestMetrics) -> None:
        """Record metrics for a request"""
        async with self._lock:
            endpoint_key = f"{metrics.method}:{metrics.path}"
            stats = self._endpoint_stats[endpoint_key]
            
            stats.total_requests += 1
            stats.total_response_time_ms += metrics.response_time_ms
            stats.min_response_time_ms = min(stats.min_response_time_ms, metrics.response_time_ms)
            stats.max_response_time_ms = max(stats.max_response_time_ms, metrics.response_time_ms)
            stats.status_codes[metrics.status_code] += 1
            
            if metrics.status_code >= 400:
                stats.total_errors += 1
            
            # Keep response times for percentile calculations (limit memory)
            stats.response_times.append(metrics.response_time_ms)
            if len(stats.response_times) > 1000:
                stats.response_times = stats.response_times[-500:]
            
            # Keep recent requests
            self._recent_requests.append(metrics)
            if len(self._recent_requests) > self.max_history:
                self._recent_requests = self._recent_requests[-self.max_history // 2:]
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get all collected metrics"""
        async with self._lock:
            uptime = datetime.now() - self._start_time
            total_requests = sum(s.total_requests for s in self._endpoint_stats.values())
            total_errors = sum(s.total_errors for s in self._endpoint_stats.values())
            
            return {
                'uptime_seconds': uptime.total_seconds(),
                'total_requests': total_requests,
                'total_errors': total_errors,
                'error_rate': total_errors / total_requests if total_requests > 0 else 0,
                'endpoints': {
                    endpoint: {
                        'total_requests': stats.total_requests,
                        'total_errors': stats.total_errors,
                        'error_rate': round(stats.error_rate * 100, 2),
                        'avg_response_time_ms': round(stats.avg_response_time_ms, 2),
                        'min_response_time_ms': round(stats.min_response_time_ms, 2) if stats.min_response_time_ms != float('inf') else 0,
                        'max_response_time_ms': round(stats.max_response_time_ms, 2),
                        'p95_response_time_ms': round(stats.p95_response_time_ms, 2),
                        'p99_response_time_ms': round(stats.p99_response_time_ms, 2),
                        'status_codes': dict(stats.status_codes)
                    }
                    for endpoint, stats in sorted(
                        self._endpoint_stats.items(),
                        key=lambda x: x[1].total_requests,
                        reverse=True
                    )[:20]  # Top 20 endpoints
                },
                'recent_slow_requests': [
                    {
                        'request_id': r.request_id,
                        'method': r.method,
                        'path': r.path,
                        'response_time_ms': round(r.response_time_ms, 2),
                        'status_code': r.status_code,
                        'timestamp': r.timestamp.isoformat()
                    }
                    for r in sorted(
                        self._recent_requests[-100:],
                        key=lambda x: x.response_time_ms,
                        reverse=True
                    )[:10]
                ]
            }
    
    async def reset_metrics(self) -> None:
        """Reset all metrics"""
        async with self._lock:
            self._endpoint_stats.clear()
            self._recent_requests.clear()
            self._start_time = datetime.now()


# Global metrics collector
metrics_collector = MetricsCollector()


class TimingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to track request timing and add performance headers
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate request ID
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        
        # Record start time
        start_time = time.perf_counter()
        
        # Get request info
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")
        
        try:
            response = await call_next(request)
        except Exception as e:
            # Log error and re-raise
            elapsed = (time.perf_counter() - start_time) * 1000
            logger.error(f"[{request_id}] {request.method} {request.url.path} - ERROR after {elapsed:.2f}ms: {e}")
            raise
        
        # Calculate elapsed time
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        
        # Add timing headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms:.2f}ms"
        response.headers["X-Server-Timing"] = f"total;dur={elapsed_ms:.2f}"
        
        # Record metrics
        request_size = int(request.headers.get("content-length", 0))
        response_size = int(response.headers.get("content-length", 0))
        
        metrics = RequestMetrics(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            response_time_ms=elapsed_ms,
            request_size=request_size,
            response_size=response_size,
            timestamp=datetime.now(),
            client_ip=client_ip,
            user_agent=user_agent[:100]
        )
        
        # Record asynchronously
        asyncio.create_task(metrics_collector.record_request(metrics))
        
        # Log slow requests
        if elapsed_ms > 1000:
            logger.warning(f"[{request_id}] SLOW: {request.method} {request.url.path} - {elapsed_ms:.2f}ms")
        
        return response


class CompressionMiddleware:
    """
    Response compression middleware supporting gzip
    """
    
    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int = 500,
        compression_level: int = 6
    ):
        self.app = app
        self.minimum_size = minimum_size
        self.compression_level = compression_level
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Check if client accepts gzip
        headers = dict(scope.get("headers", []))
        accept_encoding = headers.get(b"accept-encoding", b"").decode()
        
        if "gzip" not in accept_encoding.lower():
            await self.app(scope, receive, send)
            return
        
        # Intercept response to compress
        response_started = False
        response_headers: List[Tuple[bytes, bytes]] = []
        response_body: List[bytes] = []
        
        async def send_wrapper(message: Dict) -> None:
            nonlocal response_started, response_headers, response_body
            
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                # Don't send yet - wait for body
                return
            
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                response_body.append(body)
                
                if more_body:
                    return
                
                # Combine body
                full_body = b"".join(response_body)
                
                # Check if we should compress
                content_type = ""
                for name, value in response_headers:
                    if name.lower() == b"content-type":
                        content_type = value.decode()
                        break
                
                compressible_types = [
                    "application/json",
                    "text/plain",
                    "text/html",
                    "text/css",
                    "application/javascript"
                ]
                
                should_compress = (
                    len(full_body) >= self.minimum_size and
                    any(ct in content_type for ct in compressible_types)
                )
                
                if should_compress:
                    # Compress body
                    compressed = gzip.compress(full_body, compresslevel=self.compression_level)
                    
                    # Update headers
                    new_headers = []
                    for name, value in response_headers:
                        if name.lower() not in [b"content-length", b"content-encoding"]:
                            new_headers.append((name, value))
                    
                    new_headers.append((b"content-encoding", b"gzip"))
                    new_headers.append((b"content-length", str(len(compressed)).encode()))
                    new_headers.append((b"vary", b"Accept-Encoding"))
                    
                    full_body = compressed
                    response_headers = new_headers
                
                # Send response
                await send({
                    "type": "http.response.start",
                    "status": message.get("status", 200),
                    "headers": response_headers
                })
                
                await send({
                    "type": "http.response.body",
                    "body": full_body
                })
        
        await self.app(scope, receive, send_wrapper)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware with sliding window algorithm
    """
    
    def __init__(
        self,
        app: ASGIApp,
        requests_per_minute: int = 100,
        burst_size: int = 20
    ):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.window_size = 60  # seconds
        self._request_counts: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Get client identifier
        client_ip = request.client.host if request.client else "unknown"
        
        # Skip rate limiting for certain paths
        skip_paths = ["/api/health", "/api/metrics", "/docs", "/openapi.json"]
        if any(request.url.path.startswith(p) for p in skip_paths):
            return await call_next(request)
        
        async with self._lock:
            now = time.time()
            window_start = now - self.window_size
            
            # Clean old requests
            self._request_counts[client_ip] = [
                ts for ts in self._request_counts[client_ip]
                if ts > window_start
            ]
            
            # Check rate limit
            request_count = len(self._request_counts[client_ip])
            
            if request_count >= self.requests_per_minute:
                # Calculate retry-after
                oldest = min(self._request_counts[client_ip])
                retry_after = int(oldest + self.window_size - now) + 1
                
                return Response(
                    content=json.dumps({
                        "error": "Rate limit exceeded",
                        "retry_after": retry_after
                    }),
                    status_code=429,
                    headers={
                        "Content-Type": "application/json",
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(self.requests_per_minute),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(oldest + self.window_size))
                    }
                )
            
            # Record this request
            self._request_counts[client_ip].append(now)
        
        # Process request
        response = await call_next(request)
        
        # Add rate limit headers
        remaining = self.requests_per_minute - len(self._request_counts.get(client_ip, []))
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Cache control for API responses
        if request.url.path.startswith("/api/"):
            # Default: no caching for dynamic content
            if "Cache-Control" not in response.headers:
                response.headers["Cache-Control"] = "no-store, max-age=0"
        
        return response


class CacheControlMiddleware(BaseHTTPMiddleware):
    """
    Smart cache control based on endpoint and method
    """
    
    # Endpoints that can be cached
    CACHEABLE_ENDPOINTS = {
        "/api/stats": 30,  # 30 seconds
        "/api/categories": 300,  # 5 minutes
        "/api/health": 10,  # 10 seconds
    }
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Only cache GET requests
        if request.method != "GET":
            response.headers["Cache-Control"] = "no-store"
            return response
        
        # Check if this endpoint should be cached
        for endpoint, max_age in self.CACHEABLE_ENDPOINTS.items():
            if request.url.path.startswith(endpoint):
                response.headers["Cache-Control"] = f"public, max-age={max_age}"
                return response
        
        return response


def setup_middleware(app: FastAPI) -> None:
    """
    Configure all middleware for the application
    Order matters - middleware is executed in reverse order
    """
    # Add middleware (last added = first executed)
    app.add_middleware(TimingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CacheControlMiddleware)
    
    # Rate limiting - be generous for development
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=200,
        burst_size=50
    )
    
    # Compression - wrap the app
    # Note: This should be added via app = CompressionMiddleware(app)
    
    logger.info("✅ Performance middleware stack configured")
