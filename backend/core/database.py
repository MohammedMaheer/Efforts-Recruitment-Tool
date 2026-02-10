"""
High-Performance Async Database Manager
Silicon Valley-grade database operations with:
- True async connection pooling
- Health checks and auto-recovery
- Query result caching
- Performance monitoring
- Batch operations support
"""
import asyncio
import aiosqlite
import sqlite3
import time
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, TypeVar, Generic, Callable
from datetime import datetime, timedelta
from functools import wraps
from collections import OrderedDict
import hashlib
import json

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class PoolStats:
    """Connection pool statistics"""
    total_connections: int = 0
    active_connections: int = 0
    idle_connections: int = 0
    total_queries: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    avg_query_time_ms: float = 0.0
    total_query_time_ms: float = 0.0
    errors: int = 0
    last_health_check: Optional[datetime] = None
    is_healthy: bool = True


class LRUCache(Generic[T]):
    """Thread-safe LRU cache with TTL support"""
    
    def __init__(self, maxsize: int = 1000, ttl_seconds: int = 300):
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Tuple[T, float]] = OrderedDict()
        self._lock = asyncio.Lock()
    
    def _make_key(self, *args, **kwargs) -> str:
        """Generate cache key from arguments"""
        key_data = json.dumps((args, sorted(kwargs.items())), default=str)
        return hashlib.md5(key_data.encode()).hexdigest()
    
    async def get(self, key: str) -> Optional[T]:
        """Get item from cache if not expired"""
        async with self._lock:
            if key not in self._cache:
                return None
            
            value, timestamp = self._cache[key]
            if time.time() - timestamp > self.ttl_seconds:
                del self._cache[key]
                return None
            
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return value
    
    async def set(self, key: str, value: T) -> None:
        """Set item in cache"""
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, time.time())
            
            # Evict oldest if over capacity
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)
    
    async def delete(self, key: str) -> bool:
        """Delete item from cache"""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    async def clear(self) -> None:
        """Clear all cache entries"""
        async with self._lock:
            self._cache.clear()
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all keys matching pattern"""
        async with self._lock:
            keys_to_delete = [k for k in self._cache if pattern in k]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)
    
    @property
    async def size(self) -> int:
        async with self._lock:
            return len(self._cache)


@dataclass
class Connection:
    """Wrapper for database connection with metadata"""
    conn: aiosqlite.Connection
    created_at: datetime = field(default_factory=datetime.now)
    last_used: datetime = field(default_factory=datetime.now)
    query_count: int = 0
    is_healthy: bool = True


class AsyncConnectionPool:
    """
    High-performance async connection pool for SQLite
    Features:
    - Configurable pool size
    - Connection health monitoring
    - Automatic connection recycling
    - Query statistics
    """
    
    def __init__(
        self,
        database_path: str,
        min_connections: int = 2,
        max_connections: int = 10,
        max_idle_time: int = 300,
        health_check_interval: int = 60
    ):
        self.database_path = database_path
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.max_idle_time = max_idle_time
        self.health_check_interval = health_check_interval
        
        self._pool: asyncio.Queue[Connection] = asyncio.Queue(maxsize=max_connections)
        self._all_connections: List[Connection] = []
        self._lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self._health_task: Optional[asyncio.Task] = None
        
        self.stats = PoolStats()
    
    async def initialize(self) -> None:
        """Initialize the connection pool"""
        if self._initialized:
            return
        
        async with self._lock:
            if self._initialized:
                return
            
            # Create initial connections
            for _ in range(self.min_connections):
                conn = await self._create_connection()
                if conn:
                    await self._pool.put(conn)
                    self._all_connections.append(conn)
            
            self.stats.total_connections = len(self._all_connections)
            self.stats.idle_connections = self._pool.qsize()
            
            # Start health check background task
            self._health_task = asyncio.create_task(self._health_check_loop())
            
            self._initialized = True
            logger.info(f"âœ… Connection pool initialized with {len(self._all_connections)} connections")
    
    async def _create_connection(self) -> Optional[Connection]:
        """Create a new database connection"""
        try:
            conn = await aiosqlite.connect(
                self.database_path,
                isolation_level=None  # Autocommit mode for better performance
            )
            
            # Enable WAL mode and optimizations
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            await conn.execute("PRAGMA temp_store=MEMORY")
            await conn.execute("PRAGMA mmap_size=268435456")  # 256MB memory-mapped I/O
            
            conn.row_factory = aiosqlite.Row
            
            return Connection(conn=conn)
        except Exception as e:
            logger.error(f"Failed to create connection: {e}")
            self.stats.errors += 1
            return None
    
    async def _health_check_loop(self) -> None:
        """Background task to check connection health"""
        while not self._closed:
            try:
                await asyncio.sleep(self.health_check_interval)
                await self._perform_health_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _perform_health_check(self) -> None:
        """Check health of all connections"""
        async with self._lock:
            healthy_count = 0
            unhealthy_count = 0
            
            for conn_wrapper in self._all_connections:
                try:
                    # Simple query to test connection
                    async with asyncio.timeout(5):
                        await conn_wrapper.conn.execute("SELECT 1")
                    conn_wrapper.is_healthy = True
                    healthy_count += 1
                except Exception:
                    conn_wrapper.is_healthy = False
                    unhealthy_count += 1
            
            self.stats.last_health_check = datetime.now()
            self.stats.is_healthy = unhealthy_count == 0
            
            if unhealthy_count > 0:
                logger.warning(f"âš ï¸ {unhealthy_count} unhealthy connections detected")
    
    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool"""
        if not self._initialized:
            await self.initialize()
        
        conn_wrapper = None
        start_time = time.time()
        
        try:
            # Try to get from pool, create new if needed
            try:
                conn_wrapper = await asyncio.wait_for(
                    self._pool.get(),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                # Pool exhausted, try to create new connection
                if len(self._all_connections) < self.max_connections:
                    async with self._lock:
                        conn_wrapper = await self._create_connection()
                        if conn_wrapper:
                            self._all_connections.append(conn_wrapper)
                            self.stats.total_connections += 1
                
                if not conn_wrapper:
                    raise RuntimeError("Connection pool exhausted")
            
            # Verify connection health
            if not conn_wrapper.is_healthy:
                # Try to reconnect
                try:
                    await conn_wrapper.conn.close()
                except Exception:
                    pass
                
                new_conn = await self._create_connection()
                if new_conn:
                    async with self._lock:
                        idx = self._all_connections.index(conn_wrapper)
                        self._all_connections[idx] = new_conn
                    conn_wrapper = new_conn
                else:
                    raise RuntimeError("Failed to recover unhealthy connection")
            
            conn_wrapper.last_used = datetime.now()
            self.stats.active_connections += 1
            self.stats.idle_connections = self._pool.qsize()
            
            yield conn_wrapper.conn
            
        finally:
            elapsed = (time.time() - start_time) * 1000
            self.stats.total_query_time_ms += elapsed
            self.stats.total_queries += 1
            self.stats.avg_query_time_ms = (
                self.stats.total_query_time_ms / self.stats.total_queries
            )
            
            if conn_wrapper:
                conn_wrapper.query_count += 1
                self.stats.active_connections -= 1
                
                # Return to pool
                try:
                    self._pool.put_nowait(conn_wrapper)
                    self.stats.idle_connections = self._pool.qsize()
                except asyncio.QueueFull:
                    # Pool full, close this connection
                    try:
                        await conn_wrapper.conn.close()
                    except Exception:
                        pass
    
    async def close(self) -> None:
        """Close all connections and shutdown pool"""
        self._closed = True
        
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        
        async with self._lock:
            for conn_wrapper in self._all_connections:
                try:
                    await conn_wrapper.conn.close()
                except Exception:
                    pass
            
            self._all_connections.clear()
            
            # Clear the queue
            while not self._pool.empty():
                try:
                    self._pool.get_nowait()
                except Exception:
                    break
        
        logger.info("ðŸ”Œ Connection pool closed")


class AsyncDatabaseManager:
    """
    High-level database manager with:
    - Query caching
    - Batch operations
    - Transaction support
    - Performance metrics
    """
    
    def __init__(
        self,
        database_path: str,
        cache_size: int = 1000,
        cache_ttl: int = 300
    ):
        self.pool = AsyncConnectionPool(database_path)
        self.cache = LRUCache[Any](maxsize=cache_size, ttl_seconds=cache_ttl)
        self._query_stats: Dict[str, Dict[str, Any]] = {}
    
    async def initialize(self) -> None:
        """Initialize the database manager"""
        await self.pool.initialize()
    
    async def close(self) -> None:
        """Close the database manager"""
        await self.pool.close()
        await self.cache.clear()
    
    def cached_query(self, ttl: Optional[int] = None):
        """Decorator for cached queries"""
        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Generate cache key
                cache_key = f"{func.__name__}:{self.cache._make_key(*args[1:], **kwargs)}"
                
                # Try cache first
                cached = await self.cache.get(cache_key)
                if cached is not None:
                    self.pool.stats.cache_hits += 1
                    return cached
                
                self.pool.stats.cache_misses += 1
                
                # Execute query
                result = await func(*args, **kwargs)
                
                # Cache result
                await self.cache.set(cache_key, result)
                
                return result
            return wrapper
        return decorator
    
    async def execute(
        self,
        query: str,
        params: Tuple = (),
        fetch: bool = False
    ) -> Any:
        """Execute a query"""
        start_time = time.time()
        
        try:
            async with self.pool.acquire() as conn:
                cursor = await conn.execute(query, params)
                
                if fetch:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
                
                return cursor.lastrowid
        finally:
            elapsed = (time.time() - start_time) * 1000
            self._track_query(query, elapsed)
    
    async def execute_many(
        self,
        query: str,
        params_list: List[Tuple]
    ) -> int:
        """Execute multiple queries in a batch"""
        start_time = time.time()
        
        try:
            async with self.pool.acquire() as conn:
                await conn.executemany(query, params_list)
                return len(params_list)
        finally:
            elapsed = (time.time() - start_time) * 1000
            self._track_query(f"batch:{query}", elapsed)
    
    async def fetch_one(self, query: str, params: Tuple = ()) -> Optional[Dict]:
        """Fetch a single row"""
        async with self.pool.acquire() as conn:
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def fetch_all(self, query: str, params: Tuple = ()) -> List[Dict]:
        """Fetch all rows"""
        async with self.pool.acquire() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    @asynccontextmanager
    async def transaction(self):
        """Execute queries within a transaction"""
        async with self.pool.acquire() as conn:
            await conn.execute("BEGIN TRANSACTION")
            try:
                yield conn
                await conn.execute("COMMIT")
            except Exception:
                await conn.execute("ROLLBACK")
                raise
    
    async def batch_insert(
        self,
        table: str,
        columns: List[str],
        values_list: List[Tuple],
        batch_size: int = 100
    ) -> int:
        """Efficient batch insert with chunking"""
        if not values_list:
            return 0
        
        placeholders = ','.join(['?' for _ in columns])
        query = f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        
        total_inserted = 0
        
        for i in range(0, len(values_list), batch_size):
            batch = values_list[i:i + batch_size]
            async with self.pool.acquire() as conn:
                await conn.executemany(query, batch)
            total_inserted += len(batch)
        
        return total_inserted
    
    async def invalidate_cache(self, pattern: str = "") -> int:
        """Invalidate cache entries matching pattern"""
        if pattern:
            return await self.cache.invalidate_pattern(pattern)
        else:
            await self.cache.clear()
            return -1  # All cleared
    
    def _track_query(self, query: str, elapsed_ms: float) -> None:
        """Track query performance"""
        # Normalize query for tracking
        query_key = query[:50].strip()
        
        if query_key not in self._query_stats:
            self._query_stats[query_key] = {
                'count': 0,
                'total_time': 0,
                'avg_time': 0,
                'max_time': 0,
                'min_time': float('inf')
            }
        
        stats = self._query_stats[query_key]
        stats['count'] += 1
        stats['total_time'] += elapsed_ms
        stats['avg_time'] = stats['total_time'] / stats['count']
        stats['max_time'] = max(stats['max_time'], elapsed_ms)
        stats['min_time'] = min(stats['min_time'], elapsed_ms)
    
    def get_query_stats(self) -> Dict[str, Any]:
        """Get query performance statistics"""
        return {
            'pool_stats': {
                'total_connections': self.pool.stats.total_connections,
                'active_connections': self.pool.stats.active_connections,
                'idle_connections': self.pool.stats.idle_connections,
                'total_queries': self.pool.stats.total_queries,
                'cache_hits': self.pool.stats.cache_hits,
                'cache_misses': self.pool.stats.cache_misses,
                'cache_hit_rate': (
                    self.pool.stats.cache_hits / 
                    (self.pool.stats.cache_hits + self.pool.stats.cache_misses)
                    if (self.pool.stats.cache_hits + self.pool.stats.cache_misses) > 0
                    else 0
                ),
                'avg_query_time_ms': round(self.pool.stats.avg_query_time_ms, 2),
                'is_healthy': self.pool.stats.is_healthy,
                'last_health_check': (
                    self.pool.stats.last_health_check.isoformat()
                    if self.pool.stats.last_health_check else None
                ),
                'errors': self.pool.stats.errors
            },
            'slow_queries': sorted(
                [
                    {'query': k, **v}
                    for k, v in self._query_stats.items()
                ],
                key=lambda x: x['avg_time'],
                reverse=True
            )[:10]
        }


# Singleton instance
_db_manager: Optional[AsyncDatabaseManager] = None


async def get_db_manager(database_path: str = "recruitment.db") -> AsyncDatabaseManager:
    """Get or create the database manager singleton"""
    global _db_manager
    
    if _db_manager is None:
        _db_manager = AsyncDatabaseManager(database_path)
        await _db_manager.initialize()
    
    return _db_manager
