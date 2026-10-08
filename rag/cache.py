"""Answer cache.

Invariant: the service builds every cache key, and in secure mode the key starts with the tenant ID
and that tenant's index version, so one tenant can never read another's entry and stale answers
disappear when a tenant's documents change.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Hashable
from typing import Any


class AnswerCache:
    def __init__(self, max_entries: int = 1024) -> None:
        self._max = max_entries
        self._entries: OrderedDict[Hashable, Any] = OrderedDict()
        self._versions: dict[str, int] = {}
        self._lock = threading.Lock()

    def version(self, tenant_id: str) -> int:
        with self._lock:
            return self._versions.get(tenant_id, 0)

    def bump(self, tenant_id: str) -> None:
        with self._lock:
            self._versions[tenant_id] = self._versions.get(tenant_id, 0) + 1

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
