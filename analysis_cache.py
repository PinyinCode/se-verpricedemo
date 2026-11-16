# -*- coding: utf-8 -*-
"""Small bounded TTL cache for deterministic, versioned analysis results."""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from time import monotonic


class TTLCache:
    def __init__(self, max_items=128, ttl_seconds=300):
        if isinstance(max_items, bool) or int(max_items) <= 0:
            raise ValueError("max_items must be a positive integer")
        if float(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.max_items = int(max_items)
        self.ttl_seconds = float(ttl_seconds)
        self._items = OrderedDict()
        self._lock = RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key, default=None):
        now = monotonic()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self._misses += 1
                return deepcopy(default)
            expires_at, value = entry
            if expires_at <= now:
                self._items.pop(key, None)
                self._misses += 1
                return deepcopy(default)
            self._items.move_to_end(key)
            self._hits += 1
            return deepcopy(value)

    def set(self, key, value, ttl_seconds=None):
        ttl = self.ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        with self._lock:
            self._items.pop(key, None)
            self._items[key] = (monotonic() + ttl, deepcopy(value))
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def clear(self):
        with self._lock:
            self._items.clear()

    def stats(self):
        now = monotonic()
        with self._lock:
            expired = [key for key, (expires_at, _value) in self._items.items()
                       if expires_at <= now]
            for key in expired:
                self._items.pop(key, None)
            return {
                "size": len(self._items),
                "max_items": self.max_items,
                "hits": self._hits,
                "misses": self._misses,
            }
