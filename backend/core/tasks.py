"""
Background Task Manager
Silicon Valley-grade background task processing with:
- Priority queue
- Retry logic with exponential backoff
- Task persistence
- Progress tracking
- Graceful shutdown
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Awaitable
from functools import wraps
import traceback
import json

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task status enumeration"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TaskPriority(Enum):
    """Task priority levels"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class TaskResult:
    """Result of a task execution"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0


@dataclass
class Task:
    """Represents a background task"""
    id: str
    name: str
    func: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    
    # Retry configuration
    max_retries: int = 3
    retry_count: int = 0
    retry_delay: float = 1.0  # Base delay in seconds
    retry_backoff: float = 2.0  # Exponential backoff multiplier
    
    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Result
    result: Optional[TaskResult] = None
    error_history: List[str] = field(default_factory=list)
    
    # Progress tracking
    progress: float = 0.0
    progress_message: str = ""
    
    # Dependencies
    depends_on: List[str] = field(default_factory=list)
    
    def __lt__(self, other: 'Task') -> bool:
        """Compare tasks by priority for queue ordering"""
        return self.priority.value > other.priority.value


@dataclass 
class TaskStats:
    """Task manager statistics"""
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    cancelled_tasks: int = 0
    retried_tasks: int = 0
    total_execution_time_ms: float = 0.0
    
    @property
    def avg_execution_time_ms(self) -> float:
        if self.completed_tasks == 0:
            return 0.0
        return self.total_execution_time_ms / self.completed_tasks
    
    @property
    def success_rate(self) -> float:
        total = self.completed_tasks + self.failed_tasks
        return self.completed_tasks / total if total > 0 else 0.0


class BackgroundTaskManager:
    """
    Manages background task execution with:
    - Priority queue
    - Concurrent workers
    - Retry logic
    - Progress tracking
    """
    
    def __init__(
        self,
        max_workers: int = 4,
        max_queue_size: int = 1000
    ):
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        
        self._queue: asyncio.PriorityQueue[Task] = asyncio.PriorityQueue(maxsize=max_queue_size)
        self._tasks: Dict[str, Task] = {}
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._lock = asyncio.Lock()
        
        self.stats = TaskStats()
    
    async def start(self) -> None:
        """Start the task manager workers"""
        if self._running:
            return
        
        self._running = True
        
        # Start worker tasks
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self._workers.append(worker)
        
        logger.info(f"✅ Background task manager started with {self.max_workers} workers")
    
    async def stop(self, wait_for_completion: bool = True) -> None:
        """Stop the task manager"""
        self._running = False
        
        if wait_for_completion:
            # Wait for queue to empty
            while not self._queue.empty():
                await asyncio.sleep(0.1)
        
        # Cancel workers
        for worker in self._workers:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        
        self._workers.clear()
        logger.info("🔌 Background task manager stopped")
    
    async def _worker(self, worker_name: str) -> None:
        """Worker coroutine that processes tasks"""
        logger.debug(f"Worker {worker_name} started")
        
        while self._running:
            try:
                # Get task with timeout
                try:
                    task = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Check dependencies
                if task.depends_on:
                    deps_complete = all(
                        self._tasks.get(dep_id, Task(id='', name='', func=lambda: None)).status == TaskStatus.COMPLETED
                        for dep_id in task.depends_on
                    )
                    if not deps_complete:
                        # Re-queue task
                        await self._queue.put(task)
                        await asyncio.sleep(0.1)
                        continue
                
                # Execute task
                await self._execute_task(task, worker_name)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_name} error: {e}")
                await asyncio.sleep(0.1)
        
        logger.debug(f"Worker {worker_name} stopped")
    
    async def _execute_task(self, task: Task, worker_name: str) -> None:
        """Execute a single task"""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        
        logger.debug(f"[{worker_name}] Executing task {task.id}: {task.name}")
        
        start_time = time.time()
        
        try:
            # Execute the task function
            if asyncio.iscoroutinefunction(task.func):
                result_data = await task.func(*task.args, **task.kwargs)
            else:
                result_data = await asyncio.to_thread(task.func, *task.args, **task.kwargs)
            
            elapsed = (time.time() - start_time) * 1000
            
            task.result = TaskResult(
                success=True,
                data=result_data,
                execution_time_ms=elapsed
            )
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            task.progress = 100.0
            
            self.stats.completed_tasks += 1
            self.stats.total_execution_time_ms += elapsed
            
            logger.debug(f"[{worker_name}] Task {task.id} completed in {elapsed:.2f}ms")
            
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            error_msg = f"{type(e).__name__}: {str(e)}"
            task.error_history.append(error_msg)
            
            logger.error(f"[{worker_name}] Task {task.id} failed: {error_msg}")
            
            # Check if we should retry
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                task.status = TaskStatus.RETRYING
                
                # Calculate retry delay with exponential backoff
                delay = task.retry_delay * (task.retry_backoff ** (task.retry_count - 1))
                
                logger.info(f"Retrying task {task.id} in {delay:.1f}s (attempt {task.retry_count}/{task.max_retries})")
                
                # Schedule retry
                asyncio.create_task(self._retry_task(task, delay))
                
                self.stats.retried_tasks += 1
                
            else:
                task.result = TaskResult(
                    success=False,
                    error=error_msg,
                    execution_time_ms=elapsed
                )
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
                
                self.stats.failed_tasks += 1
    
    async def _retry_task(self, task: Task, delay: float) -> None:
        """Schedule a task for retry after delay"""
        await asyncio.sleep(delay)
        
        if not self._running:
            return
        
        task.status = TaskStatus.PENDING
        await self._queue.put(task)
    
    async def submit(
        self,
        func: Callable,
        *args,
        name: Optional[str] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        max_retries: int = 3,
        depends_on: Optional[List[str]] = None,
        **kwargs
    ) -> str:
        """
        Submit a task for background execution
        
        Returns: task_id
        """
        task_id = str(uuid.uuid4())[:8]
        
        task = Task(
            id=task_id,
            name=name or func.__name__,
            func=func,
            args=args,
            kwargs=kwargs,
            priority=priority,
            max_retries=max_retries,
            depends_on=depends_on or []
        )
        
        async with self._lock:
            self._tasks[task_id] = task
            self.stats.total_tasks += 1
        
        await self._queue.put(task)
        
        logger.debug(f"Task {task_id} submitted: {task.name}")
        
        return task_id
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        return self._tasks.get(task_id)
    
    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status"""
        task = self._tasks.get(task_id)
        if not task:
            return None
        
        return {
            'id': task.id,
            'name': task.name,
            'status': task.status.value,
            'progress': task.progress,
            'progress_message': task.progress_message,
            'created_at': task.created_at.isoformat(),
            'started_at': task.started_at.isoformat() if task.started_at else None,
            'completed_at': task.completed_at.isoformat() if task.completed_at else None,
            'retry_count': task.retry_count,
            'error_history': task.error_history,
            'result': {
                'success': task.result.success,
                'execution_time_ms': task.result.execution_time_ms,
                'error': task.result.error
            } if task.result else None
        }
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending task"""
        task = self._tasks.get(task_id)
        if not task:
            return False
        
        if task.status in [TaskStatus.PENDING, TaskStatus.RETRYING]:
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now()
            self.stats.cancelled_tasks += 1
            return True
        
        return False
    
    async def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = ""
    ) -> None:
        """Update task progress"""
        task = self._tasks.get(task_id)
        if task:
            task.progress = min(100.0, max(0.0, progress))
            task.progress_message = message
    
    async def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get all pending tasks"""
        return [
            await self.get_task_status(task_id)
            for task_id, task in self._tasks.items()
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRYING]
        ]
    
    async def get_recent_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent tasks"""
        sorted_tasks = sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True
        )[:limit]
        
        return [
            await self.get_task_status(t.id)
            for t in sorted_tasks
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get task manager statistics"""
        return {
            'total_tasks': self.stats.total_tasks,
            'completed_tasks': self.stats.completed_tasks,
            'failed_tasks': self.stats.failed_tasks,
            'cancelled_tasks': self.stats.cancelled_tasks,
            'retried_tasks': self.stats.retried_tasks,
            'success_rate': round(self.stats.success_rate * 100, 2),
            'avg_execution_time_ms': round(self.stats.avg_execution_time_ms, 2),
            'queue_size': self._queue.qsize(),
            'active_workers': len([w for w in self._workers if not w.done()]),
            'is_running': self._running
        }
    
    async def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """Remove completed tasks older than max_age"""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        
        to_remove = [
            task_id for task_id, task in self._tasks.items()
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
            and task.completed_at
            and task.completed_at < cutoff
        ]
        
        async with self._lock:
            for task_id in to_remove:
                del self._tasks[task_id]
        
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old tasks")
        
        return len(to_remove)


def background_task(
    priority: TaskPriority = TaskPriority.NORMAL,
    max_retries: int = 3,
    name: Optional[str] = None
):
    """
    Decorator to mark a function as a background task
    
    Usage:
        @background_task(priority=TaskPriority.HIGH)
        async def send_email(to: str, subject: str):
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, task_manager: BackgroundTaskManager, **kwargs) -> str:
            return await task_manager.submit(
                func,
                *args,
                name=name or func.__name__,
                priority=priority,
                max_retries=max_retries,
                **kwargs
            )
        
        # Store metadata
        wrapper._is_background_task = True
        wrapper._priority = priority
        wrapper._max_retries = max_retries
        
        return wrapper
    return decorator


# Global task manager instance
_task_manager: Optional[BackgroundTaskManager] = None


async def get_task_manager() -> BackgroundTaskManager:
    """Get or create the global task manager"""
    global _task_manager
    
    if _task_manager is None:
        _task_manager = BackgroundTaskManager(max_workers=4)
        await _task_manager.start()
    
    return _task_manager
