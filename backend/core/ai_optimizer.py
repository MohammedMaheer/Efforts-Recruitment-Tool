"""
Optimized AI Service Wrapper
Silicon Valley-grade AI processing with:
- Batch embedding computation
- Async processing queue
- Memory-efficient model loading
- Result caching
- Performance monitoring
"""
import asyncio
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Callable
from functools import lru_cache
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AITask:
    """Represents an AI processing task"""
    id: str
    task_type: str
    input_data: Any
    priority: int = 0
    created_at: float = field(default_factory=time.time)
    result: Optional[Any] = None
    error: Optional[str] = None
    completed: bool = False
    processing_time_ms: float = 0.0


@dataclass
class AIStats:
    """AI service statistics"""
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_processing_time_ms: float = 0.0
    embedding_cache_hits: int = 0
    embedding_cache_misses: int = 0
    batch_count: int = 0
    avg_batch_size: float = 0.0
    model_load_time_ms: float = 0.0
    
    @property
    def avg_processing_time_ms(self) -> float:
        if self.completed_tasks == 0:
            return 0.0
        return self.total_processing_time_ms / self.completed_tasks
    
    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.completed_tasks / self.total_tasks
    
    @property
    def cache_hit_rate(self) -> float:
        total = self.embedding_cache_hits + self.embedding_cache_misses
        return self.embedding_cache_hits / total if total > 0 else 0.0


class EmbeddingCache:
    """
    Memory-efficient embedding cache using numpy arrays
    """
    
    def __init__(self, max_entries: int = 5000, embedding_dim: int = 384):
        self.max_entries = max_entries
        self.embedding_dim = embedding_dim
        
        # Pre-allocate memory for embeddings
        self._embeddings = np.zeros((max_entries, embedding_dim), dtype=np.float32)
        self._keys: Dict[str, int] = {}
        self._access_order: List[str] = []
        self._next_idx = 0
        self._lock = asyncio.Lock()
    
    def _hash_text(self, text: str) -> str:
        """Generate hash key for text"""
        return hashlib.md5(text.encode()).hexdigest()
    
    async def get(self, text: str) -> Optional[np.ndarray]:
        """Get embedding from cache"""
        async with self._lock:
            key = self._hash_text(text)
            
            if key not in self._keys:
                return None
            
            idx = self._keys[key]
            
            # Move to end of access order
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)
            
            return self._embeddings[idx].copy()
    
    async def set(self, text: str, embedding: np.ndarray) -> None:
        """Set embedding in cache"""
        async with self._lock:
            key = self._hash_text(text)
            
            # Check if key exists
            if key in self._keys:
                idx = self._keys[key]
            else:
                # Evict if needed
                if len(self._keys) >= self.max_entries:
                    # Remove oldest entry
                    oldest_key = self._access_order.pop(0)
                    oldest_idx = self._keys.pop(oldest_key)
                    idx = oldest_idx
                else:
                    idx = self._next_idx
                    self._next_idx += 1
                
                self._keys[key] = idx
            
            # Store embedding
            self._embeddings[idx] = embedding.astype(np.float32)
            
            # Update access order
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)
    
    async def get_batch(self, texts: List[str]) -> Tuple[Dict[str, np.ndarray], List[str]]:
        """
        Get multiple embeddings at once
        Returns: (cached_embeddings, missing_texts)
        """
        cached = {}
        missing = []
        
        async with self._lock:
            for text in texts:
                key = self._hash_text(text)
                if key in self._keys:
                    idx = self._keys[key]
                    cached[text] = self._embeddings[idx].copy()
                else:
                    missing.append(text)
        
        return cached, missing
    
    async def set_batch(self, embeddings: Dict[str, np.ndarray]) -> None:
        """Set multiple embeddings at once"""
        for text, embedding in embeddings.items():
            await self.set(text, embedding)
    
    @property
    async def size(self) -> int:
        async with self._lock:
            return len(self._keys)
    
    async def clear(self) -> None:
        async with self._lock:
            self._keys.clear()
            self._access_order.clear()
            self._next_idx = 0


class OptimizedAIService:
    """
    Optimized AI service wrapper providing:
    - Batch processing for efficiency
    - Embedding caching
    - Async task queue
    - Performance monitoring
    """
    
    def __init__(
        self,
        base_service: Any,
        max_batch_size: int = 32,
        max_queue_size: int = 1000,
        num_workers: int = 2
    ):
        self.base_service = base_service
        self.max_batch_size = max_batch_size
        self.max_queue_size = max_queue_size
        
        # Task queue
        self._queue: asyncio.Queue[AITask] = asyncio.Queue(maxsize=max_queue_size)
        self._processing = False
        self._workers: List[asyncio.Task] = []
        
        # Embedding cache
        self._embedding_cache = EmbeddingCache(max_entries=5000)
        
        # Thread pool for CPU-bound operations
        self._thread_pool = ThreadPoolExecutor(max_workers=num_workers)
        
        # Statistics
        self.stats = AIStats()
        
        # Batch accumulator
        self._pending_embeddings: List[Tuple[str, asyncio.Future]] = []
        self._batch_lock = asyncio.Lock()
        self._batch_event = asyncio.Event()
    
    async def start(self) -> None:
        """Start the AI service workers"""
        if self._processing:
            return
        
        self._processing = True
        
        # Start batch processor
        self._workers.append(
            asyncio.create_task(self._batch_processor())
        )
        
        logger.info("✅ Optimized AI service started")
    
    async def stop(self) -> None:
        """Stop the AI service workers"""
        self._processing = False
        
        # Cancel workers
        for worker in self._workers:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        
        self._workers.clear()
        self._thread_pool.shutdown(wait=False)
        
        logger.info("🔌 Optimized AI service stopped")
    
    async def _batch_processor(self) -> None:
        """Background worker that processes embeddings in batches"""
        while self._processing:
            try:
                # Wait for batch accumulation or timeout
                try:
                    await asyncio.wait_for(
                        self._batch_event.wait(),
                        timeout=0.1  # Process batch every 100ms max
                    )
                except asyncio.TimeoutError:
                    pass
                
                self._batch_event.clear()
                
                # Get pending embeddings
                async with self._batch_lock:
                    if not self._pending_embeddings:
                        continue
                    
                    batch = self._pending_embeddings[:self.max_batch_size]
                    self._pending_embeddings = self._pending_embeddings[self.max_batch_size:]
                
                if not batch:
                    continue
                
                # Process batch
                texts = [text for text, _ in batch]
                futures = [future for _, future in batch]
                
                try:
                    # Check cache first
                    cached, missing = await self._embedding_cache.get_batch(texts)
                    
                    self.stats.embedding_cache_hits += len(cached)
                    self.stats.embedding_cache_misses += len(missing)
                    
                    # Compute missing embeddings
                    if missing and hasattr(self.base_service, 'sentence_model'):
                        start_time = time.time()
                        
                        # Run in thread pool
                        loop = asyncio.get_event_loop()
                        new_embeddings = await loop.run_in_executor(
                            self._thread_pool,
                            lambda: self.base_service.sentence_model.encode(
                                missing,
                                batch_size=min(32, len(missing)),
                                show_progress_bar=False,
                                normalize_embeddings=True
                            )
                        )
                        
                        elapsed = (time.time() - start_time) * 1000
                        
                        # Cache new embeddings
                        for i, text in enumerate(missing):
                            cached[text] = new_embeddings[i]
                            await self._embedding_cache.set(text, new_embeddings[i])
                        
                        self.stats.batch_count += 1
                        total_batches = self.stats.batch_count
                        self.stats.avg_batch_size = (
                            (self.stats.avg_batch_size * (total_batches - 1) + len(missing))
                            / total_batches
                        )
                    
                    # Resolve futures
                    for text, future in batch:
                        if text in cached:
                            if not future.done():
                                future.set_result(cached[text])
                        else:
                            if not future.done():
                                future.set_result(None)
                
                except Exception as e:
                    logger.error(f"Batch embedding error: {e}")
                    for _, future in batch:
                        if not future.done():
                            future.set_exception(e)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Batch processor error: {e}")
                await asyncio.sleep(0.1)
    
    async def get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding with batching and caching"""
        # Check cache first
        cached = await self._embedding_cache.get(text)
        if cached is not None:
            self.stats.embedding_cache_hits += 1
            return cached
        
        self.stats.embedding_cache_misses += 1
        
        # Add to batch queue
        future: asyncio.Future = asyncio.Future()
        
        async with self._batch_lock:
            self._pending_embeddings.append((text, future))
            self._batch_event.set()
        
        # Wait for result
        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(f"Embedding timeout for text: {text[:50]}...")
            return None
    
    async def get_embeddings_batch(self, texts: List[str]) -> Dict[str, np.ndarray]:
        """Get multiple embeddings efficiently"""
        if not texts:
            return {}
        
        # Check cache
        cached, missing = await self._embedding_cache.get_batch(texts)
        
        self.stats.embedding_cache_hits += len(cached)
        self.stats.embedding_cache_misses += len(missing)
        
        if not missing:
            return cached
        
        # Compute missing embeddings
        if hasattr(self.base_service, 'sentence_model') and self.base_service.sentence_model:
            try:
                start_time = time.time()
                
                loop = asyncio.get_event_loop()
                new_embeddings = await loop.run_in_executor(
                    self._thread_pool,
                    lambda: self.base_service.sentence_model.encode(
                        missing,
                        batch_size=min(32, len(missing)),
                        show_progress_bar=False,
                        normalize_embeddings=True
                    )
                )
                
                elapsed = (time.time() - start_time) * 1000
                self.stats.total_processing_time_ms += elapsed
                
                # Cache and return
                for i, text in enumerate(missing):
                    cached[text] = new_embeddings[i]
                    await self._embedding_cache.set(text, new_embeddings[i])
                
                self.stats.batch_count += 1
                
            except Exception as e:
                logger.error(f"Batch embedding error: {e}")
        
        return cached
    
    async def analyze_candidate(self, resume_text: str) -> Dict[str, Any]:
        """Analyze candidate with performance tracking"""
        start_time = time.time()
        self.stats.total_tasks += 1
        
        try:
            # Use base service's analyze_candidate
            if asyncio.iscoroutinefunction(self.base_service.analyze_candidate):
                result = await self.base_service.analyze_candidate(resume_text)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    self._thread_pool,
                    self.base_service.analyze_candidate,
                    resume_text
                )
            
            elapsed = (time.time() - start_time) * 1000
            self.stats.completed_tasks += 1
            self.stats.total_processing_time_ms += elapsed
            
            return result
            
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            self.stats.failed_tasks += 1
            self.stats.total_processing_time_ms += elapsed
            logger.error(f"Candidate analysis error: {e}")
            
            return {
                'skills': [],
                'experience': 0,
                'job_category': 'General',
                'quality_score': 35,
                'summary': 'Analysis unavailable'
            }
    
    async def analyze_candidates_batch(
        self,
        candidates: List[Dict[str, Any]],
        batch_size: int = 10
    ) -> List[Dict[str, Any]]:
        """Analyze multiple candidates efficiently"""
        results = []
        
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            
            # Process batch concurrently
            tasks = [
                self.analyze_candidate(c.get('resume_text', ''))
                for c in batch
            ]
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for j, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    logger.error(f"Batch analysis error: {result}")
                    results.append({
                        'skills': [],
                        'experience': 0,
                        'job_category': 'General',
                        'quality_score': 35,
                        'summary': 'Analysis failed'
                    })
                else:
                    results.append(result)
        
        return results
    
    async def compute_similarity(
        self,
        text1: str,
        text2: str
    ) -> float:
        """Compute semantic similarity between two texts"""
        embeddings = await self.get_embeddings_batch([text1, text2])
        
        if len(embeddings) < 2:
            return 0.0
        
        emb1 = embeddings.get(text1)
        emb2 = embeddings.get(text2)
        
        if emb1 is None or emb2 is None:
            return 0.0
        
        # Cosine similarity
        similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
        return float(similarity)
    
    async def rank_candidates(
        self,
        job_description: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 10
    ) -> List[Tuple[Dict[str, Any], float]]:
        """Rank candidates by similarity to job description"""
        if not candidates:
            return []
        
        # Get all embeddings at once
        texts = [job_description] + [
            c.get('resume_text', c.get('summary', ''))
            for c in candidates
        ]
        
        embeddings = await self.get_embeddings_batch(texts)
        
        jd_embedding = embeddings.get(job_description)
        if jd_embedding is None:
            return [(c, 0.0) for c in candidates[:top_k]]
        
        # Calculate similarities
        scored_candidates = []
        for candidate in candidates:
            text = candidate.get('resume_text', candidate.get('summary', ''))
            embedding = embeddings.get(text)
            
            if embedding is not None:
                similarity = float(np.dot(jd_embedding, embedding))
                scored_candidates.append((candidate, similarity))
            else:
                scored_candidates.append((candidate, 0.0))
        
        # Sort by similarity
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        
        return scored_candidates[:top_k]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get AI service statistics"""
        return {
            'total_tasks': self.stats.total_tasks,
            'completed_tasks': self.stats.completed_tasks,
            'failed_tasks': self.stats.failed_tasks,
            'success_rate': round(self.stats.success_rate * 100, 2),
            'avg_processing_time_ms': round(self.stats.avg_processing_time_ms, 2),
            'embedding_cache_hits': self.stats.embedding_cache_hits,
            'embedding_cache_misses': self.stats.embedding_cache_misses,
            'cache_hit_rate': round(self.stats.cache_hit_rate * 100, 2),
            'batch_count': self.stats.batch_count,
            'avg_batch_size': round(self.stats.avg_batch_size, 2)
        }
    
    async def clear_cache(self) -> None:
        """Clear embedding cache"""
        await self._embedding_cache.clear()
        logger.info("🗑️ Embedding cache cleared")


def create_optimized_ai_service(base_service: Any) -> OptimizedAIService:
    """Factory function to create optimized AI service"""
    return OptimizedAIService(
        base_service=base_service,
        max_batch_size=32,
        max_queue_size=1000,
        num_workers=2
    )
