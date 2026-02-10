"""
High-Performance Caching Service
Silicon Valley-grade caching with:
- Multi-level cache (Memory -> Redis-like)
- Cache warming strategies
- Intelligent invalidation
- Cache statistics and monitoring
- Distributed cache support ready
"""
import asyncio
import hashlib
import json
import time
import logging
import pickle
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import (
    Any, Callable, Dict, Generic, List, Optional, 
    Set, Tuple, TypeVar, Union
)
from functools import wraps
from enum import Enum

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CacheStrategy(Enum):
    """Cache eviction strategies"""
    LRU = "lru"  # Least Recently Used
    LFU = "lfu"  # Least Frequently Used
    TTL = "ttl"  # Time To Live only
    FIFO = "fifo"  # First In First Out


@dataclass
class CacheEntry(Generic[T]):
    """A single cache entry with metadata"""
    key: str
    value: T
    created_at: float
    expires_at: Optional[float]
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    size_bytes: int = 0
    tags: Set[str] = field(default_factory=set)
    
    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at
    
    @property
    def ttl_remaining(self) -> Optional[float]:
        if self.expires_at is None:
            return None
        return max(0, self.expires_at - time.time())


@dataclass
class CacheStats:
    """Cache statistics"""
    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0
    evictions: int = 0
    expired: int = 0
    total_size_bytes: int = 0
    entry_count: int = 0
    
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
    
    @property
    def miss_rate(self) -> float:
        return 1.0 - self.hit_rate


class CacheBackend(ABC, Generic[T]):
    """Abstract cache backend interface"""
    
    @abstractmethod
    async def get(self, key: str) -> Optional[T]:
        pass
    
    @abstractmethod
    async def set(self, key: str, value: T, ttl: Optional[int] = None, tags: Optional[Set[str]] = None) -> bool:
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> bool:
        pass
    
    @abstractmethod
    async def exists(self, key: str) -> bool:
        pass
    
    @abstractmethod
    async def clear(self) -> int:
        pass
    
    @abstractmethod
    async def get_stats(self) -> CacheStats:
        pass


class MemoryCache(CacheBackend[T]):
    """
    High-performance in-memory cache with LRU eviction
    Features:
    - Configurable max size (entries and bytes)
    - TTL support
    - Tag-based invalidation
    - Thread-safe operations
    """
    
    def __init__(
        self,
        max_entries: int = 10000,
        max_size_bytes: int = 100 * 1024 * 1024,  # 100MB
        default_ttl: int = 300,
        strategy: CacheStrategy = CacheStrategy.LRU
    ):
        self.max_entries = max_entries
        self.max_size_bytes = max_size_bytes
        self.default_ttl = default_ttl
        self.strategy = strategy
        
        self._cache: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self._tags: Dict[str, Set[str]] = {}  # tag -> set of keys
        self._lock = asyncio.Lock()
        self._stats = CacheStats()
    
    def _estimate_size(self, value: Any) -> int:
        """Estimate memory size of a value"""
        try:
            return len(pickle.dumps(value))
        except Exception:
            return 1024  # Default estimate
    
    async def _evict_if_needed(self) -> None:
        """Evict entries if cache exceeds limits"""
        while (
            len(self._cache) > self.max_entries or 
            self._stats.total_size_bytes > self.max_size_bytes
        ):
            if not self._cache:
                break
            
            # Remove oldest entry (LRU)
            key, entry = self._cache.popitem(last=False)
            self._stats.total_size_bytes -= entry.size_bytes
            self._stats.evictions += 1
            
            # Clean up tags
            for tag in entry.tags:
                if tag in self._tags:
                    self._tags[tag].discard(key)
    
    async def _cleanup_expired(self) -> int:
        """Remove expired entries"""
        expired_keys = [
            key for key, entry in self._cache.items()
            if entry.is_expired
        ]
        
        for key in expired_keys:
            entry = self._cache.pop(key)
            self._stats.total_size_bytes -= entry.size_bytes
            self._stats.expired += 1
            
            for tag in entry.tags:
                if tag in self._tags:
                    self._tags[tag].discard(key)
        
        return len(expired_keys)
    
    async def get(self, key: str) -> Optional[T]:
        """Get value from cache"""
        async with self._lock:
            if key not in self._cache:
                self._stats.misses += 1
                return None
            
            entry = self._cache[key]
            
            # Check expiration
            if entry.is_expired:
                self._cache.pop(key)
                self._stats.total_size_bytes -= entry.size_bytes
                self._stats.expired += 1
                self._stats.misses += 1
                return None
            
            # Update access metadata
            entry.access_count += 1
            entry.last_accessed = time.time()
            
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            
            self._stats.hits += 1
            return entry.value
    
    async def set(
        self,
        key: str,
        value: T,
        ttl: Optional[int] = None,
        tags: Optional[Set[str]] = None
    ) -> bool:
        """Set value in cache"""
        async with self._lock:
            # Calculate size
            size_bytes = self._estimate_size(value)
            
            # Create entry
            now = time.time()
            ttl_seconds = ttl if ttl is not None else self.default_ttl
            expires_at = now + ttl_seconds if ttl_seconds > 0 else None
            
            entry = CacheEntry(
                key=key,
                value=value,
                created_at=now,
                expires_at=expires_at,
                size_bytes=size_bytes,
                tags=tags or set()
            )
            
            # Remove old entry if exists
            if key in self._cache:
                old_entry = self._cache.pop(key)
                self._stats.total_size_bytes -= old_entry.size_bytes
            
            # Add new entry
            self._cache[key] = entry
            self._stats.total_size_bytes += size_bytes
            self._stats.sets += 1
            self._stats.entry_count = len(self._cache)
            
            # Update tags index
            for tag in entry.tags:
                if tag not in self._tags:
                    self._tags[tag] = set()
                self._tags[tag].add(key)
            
            # Evict if needed
            await self._evict_if_needed()
            
            return True
    
    async def delete(self, key: str) -> bool:
        """Delete value from cache"""
        async with self._lock:
            if key not in self._cache:
                return False
            
            entry = self._cache.pop(key)
            self._stats.total_size_bytes -= entry.size_bytes
            self._stats.deletes += 1
            self._stats.entry_count = len(self._cache)
            
            # Clean up tags
            for tag in entry.tags:
                if tag in self._tags:
                    self._tags[tag].discard(key)
            
            return True
    
    async def exists(self, key: str) -> bool:
        """Check if key exists and is not expired"""
        async with self._lock:
            if key not in self._cache:
                return False
            return not self._cache[key].is_expired
    
    async def clear(self) -> int:
        """Clear all entries"""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._tags.clear()
            self._stats.total_size_bytes = 0
            self._stats.entry_count = 0
            return count
    
    async def invalidate_by_tag(self, tag: str) -> int:
        """Invalidate all entries with a specific tag"""
        async with self._lock:
            if tag not in self._tags:
                return 0
            
            keys = list(self._tags[tag])
            count = 0
            
            for key in keys:
                if key in self._cache:
                    entry = self._cache.pop(key)
                    self._stats.total_size_bytes -= entry.size_bytes
                    self._stats.deletes += 1
                    count += 1
                    
                    for t in entry.tags:
                        if t in self._tags:
                            self._tags[t].discard(key)
            
            self._stats.entry_count = len(self._cache)
            return count
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all keys matching pattern"""
        async with self._lock:
            matching_keys = [k for k in self._cache if pattern in k]
            count = 0
            
            for key in matching_keys:
                entry = self._cache.pop(key)
                self._stats.total_size_bytes -= entry.size_bytes
                self._stats.deletes += 1
                count += 1
                
                for tag in entry.tags:
                    if tag in self._tags:
                        self._tags[tag].discard(key)
            
            self._stats.entry_count = len(self._cache)
            return count
    
    async def get_many(self, keys: List[str]) -> Dict[str, T]:
        """Get multiple values at once"""
        results = {}
        for key in keys:
            value = await self.get(key)
            if value is not None:
                results[key] = value
        return results
    
    async def set_many(
        self,
        items: Dict[str, T],
        ttl: Optional[int] = None,
        tags: Optional[Set[str]] = None
    ) -> int:
        """Set multiple values at once"""
        count = 0
        for key, value in items.items():
            if await self.set(key, value, ttl, tags):
                count += 1
        return count
    
    async def get_stats(self) -> CacheStats:
        """Get cache statistics"""
        async with self._lock:
            self._stats.entry_count = len(self._cache)
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                sets=self._stats.sets,
                deletes=self._stats.deletes,
                evictions=self._stats.evictions,
                expired=self._stats.expired,
                total_size_bytes=self._stats.total_size_bytes,
                entry_count=self._stats.entry_count
            )
    
    async def get_keys_by_tag(self, tag: str) -> List[str]:
        """Get all keys with a specific tag"""
        async with self._lock:
            return list(self._tags.get(tag, set()))


class CacheService:
    """
    High-level caching service with:
    - Decorator-based caching
    - Multi-level caching support
    - Cache warming
    - Automatic key generation
    """
    
    def __init__(self, backend: Optional[CacheBackend] = None):
        self._backend = backend or MemoryCache()
        self._warmers: List[Callable] = []
    
    @property
    def backend(self) -> CacheBackend:
        return self._backend
    
    def generate_key(self, prefix: str, *args, **kwargs) -> str:
        """Generate a cache key from function arguments"""
        key_data = json.dumps({
            'args': args,
            'kwargs': sorted(kwargs.items())
        }, default=str, sort_keys=True)
        
        key_hash = hashlib.md5(key_data.encode()).hexdigest()[:12]
        return f"{prefix}:{key_hash}"
    
    def cached(
        self,
        ttl: Optional[int] = None,
        key_prefix: Optional[str] = None,
        tags: Optional[Set[str]] = None,
        skip_if: Optional[Callable[..., bool]] = None
    ):
        """
        Decorator to cache function results
        
        Usage:
            @cache_service.cached(ttl=300, key_prefix="user")
            async def get_user(user_id: int):
                return await db.fetch_user(user_id)
        """
        def decorator(func: Callable):
            prefix = key_prefix or func.__name__
            
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Check skip condition
                if skip_if and skip_if(*args, **kwargs):
                    return await func(*args, **kwargs)
                
                # Generate cache key
                cache_key = self.generate_key(prefix, *args, **kwargs)
                
                # Try to get from cache
                cached_value = await self._backend.get(cache_key)
                if cached_value is not None:
                    return cached_value
                
                # Execute function
                result = await func(*args, **kwargs)
                
                # Cache result
                if result is not None:
                    await self._backend.set(cache_key, result, ttl=ttl, tags=tags)
                
                return result
            
            # Add cache control methods to wrapper
            wrapper.invalidate = lambda *args, **kwargs: self._backend.delete(
                self.generate_key(prefix, *args, **kwargs)
            )
            wrapper.get_key = lambda *args, **kwargs: self.generate_key(prefix, *args, **kwargs)
            
            return wrapper
        return decorator
    
    def cached_property(self, ttl: Optional[int] = None):
        """
        Decorator for caching instance method results
        
        Usage:
            class User:
                @cache_service.cached_property(ttl=300)
                async def permissions(self):
                    return await fetch_permissions(self.id)
        """
        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(instance, *args, **kwargs):
                # Generate key including instance identity
                instance_id = getattr(instance, 'id', id(instance))
                prefix = f"{instance.__class__.__name__}.{func.__name__}.{instance_id}"
                cache_key = self.generate_key(prefix, *args, **kwargs)
                
                cached_value = await self._backend.get(cache_key)
                if cached_value is not None:
                    return cached_value
                
                result = await func(instance, *args, **kwargs)
                
                if result is not None:
                    await self._backend.set(cache_key, result, ttl=ttl)
                
                return result
            return wrapper
        return decorator
    
    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], T],
        ttl: Optional[int] = None,
        tags: Optional[Set[str]] = None
    ) -> T:
        """Get from cache or compute and cache"""
        value = await self._backend.get(key)
        if value is not None:
            return value
        
        # Compute value
        if asyncio.iscoroutinefunction(factory):
            value = await factory()
        else:
            value = factory()
        
        await self._backend.set(key, value, ttl=ttl, tags=tags)
        return value
    
    def register_warmer(self, warmer: Callable[[], None]) -> None:
        """Register a cache warmer function"""
        self._warmers.append(warmer)
    
    async def warm_cache(self) -> int:
        """Execute all registered cache warmers"""
        count = 0
        for warmer in self._warmers:
            try:
                if asyncio.iscoroutinefunction(warmer):
                    await warmer()
                else:
                    warmer()
                count += 1
            except Exception as e:
                logger.error(f"Cache warmer failed: {e}")
        
        logger.info(f"âœ… Cache warming complete: {count}/{len(self._warmers)} warmers executed")
        return count
    
    async def get(self, key: str) -> Optional[Any]:
        """Direct cache get"""
        return await self._backend.get(key)
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        tags: Optional[Set[str]] = None
    ) -> bool:
        """Direct cache set"""
        return await self._backend.set(key, value, ttl=ttl, tags=tags)
    
    async def delete(self, key: str) -> bool:
        """Direct cache delete"""
        return await self._backend.delete(key)
    
    async def invalidate_tag(self, tag: str) -> int:
        """Invalidate all entries with tag"""
        if isinstance(self._backend, MemoryCache):
            return await self._backend.invalidate_by_tag(tag)
        return 0
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all entries matching pattern"""
        if isinstance(self._backend, MemoryCache):
            return await self._backend.invalidate_pattern(pattern)
        return 0
    
    async def clear(self) -> int:
        """Clear entire cache"""
        return await self._backend.clear()
    
    async def stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        stats = await self._backend.get_stats()
        return {
            'hits': stats.hits,
            'misses': stats.misses,
            'hit_rate': round(stats.hit_rate * 100, 2),
            'sets': stats.sets,
            'deletes': stats.deletes,
            'evictions': stats.evictions,
            'expired': stats.expired,
            'entry_count': stats.entry_count,
            'total_size_mb': round(stats.total_size_bytes / (1024 * 1024), 2)
        }


# Global cache service instance
_cache_service: Optional[CacheService] = None


def get_cache_service() -> CacheService:
    """Get or create the global cache service"""
    global _cache_service
    
    if _cache_service is None:
        _cache_service = CacheService(
            backend=MemoryCache(
                max_entries=10000,
                max_size_bytes=100 * 1024 * 1024,  # 100MB
                default_ttl=300
            )
        )
    
    return _cache_service


# Convenience decorators
def cached(
    ttl: Optional[int] = None,
    key_prefix: Optional[str] = None,
    tags: Optional[Set[str]] = None
):
    """Convenience decorator using global cache service"""
    return get_cache_service().cached(ttl=ttl, key_prefix=key_prefix, tags=tags)
