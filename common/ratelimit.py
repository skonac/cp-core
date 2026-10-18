"""Small fixed-window rate limiter on Django's cache (per process for now; a shared cache
such as Redis makes it global when there are several gunicorn workers)."""
import time

from django.core.cache import cache


def allow(key: str, limit: int, window: int) -> bool:
    """True while `key` has been hit at most `limit` times in the current `window` seconds."""
    bucket = f"rl:{key}:{int(time.time() // window)}"
    added = cache.add(bucket, 1, timeout=window + 5)
    if added:
        return True
    try:
        return cache.incr(bucket) <= limit
    except ValueError:   # expired between add and incr
        cache.add(bucket, 1, timeout=window + 5)
        return True
