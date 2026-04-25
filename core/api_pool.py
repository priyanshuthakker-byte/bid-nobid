"""
Round-robin API key pool — Gemini + Groq.
Per-key rate tracking, smart rotation, avoids global sleep.
Enables 10+ concurrent tender analysts on free-tier keys.
"""

import threading
import time
from collections import deque
from typing import List, Optional, Dict


class KeyState:
    __slots__ = ("key", "cooldown_until", "rpm_window", "rpd_count", "day_start",
                 "consecutive_fails", "in_use", "last_ok_at")

    def __init__(self, key: str):
        self.key = key
        self.cooldown_until = 0.0
        self.rpm_window: deque = deque()  # timestamps within 60s
        self.rpd_count = 0
        self.day_start = time.time()
        self.consecutive_fails = 0
        self.in_use = 0
        self.last_ok_at = 0.0

    def tick_rpm(self, now: float):
        cutoff = now - 60.0
        while self.rpm_window and self.rpm_window[0] < cutoff:
            self.rpm_window.popleft()

    def tick_rpd(self, now: float):
        if now - self.day_start > 86400:
            self.day_start = now
            self.rpd_count = 0


class KeyPool:
    def __init__(self, keys: List[str], rpm_limit: int = 14, rpd_limit: int = 1400):
        self.rpm_limit = rpm_limit
        self.rpd_limit = rpd_limit
        self._lock = threading.Lock()
        self._states: Dict[str, KeyState] = {k: KeyState(k) for k in keys if k and len(k.strip()) > 20}
        self._order = list(self._states.keys())
        self._cursor = 0

    def refresh(self, keys: List[str]):
        with self._lock:
            live = {k for k in keys if k and len(k.strip()) > 20}
            for k in live - set(self._states):
                self._states[k] = KeyState(k)
            for k in set(self._states) - live:
                self._states.pop(k, None)
            self._order = list(self._states.keys())
            if self._cursor >= len(self._order):
                self._cursor = 0

    def size(self) -> int:
        return len(self._states)

    def acquire(self, timeout: float = 180.0) -> Optional[str]:
        """Return next available key, rotating round-robin. None if all exhausted."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self._order:
                    return None
                n = len(self._order)
                best = None
                best_score = None
                now = time.time()
                for i in range(n):
                    idx = (self._cursor + i) % n
                    key = self._order[idx]
                    st = self._states[key]
                    st.tick_rpm(now)
                    st.tick_rpd(now)
                    if st.cooldown_until > now:
                        continue
                    if st.rpd_count >= self.rpd_limit:
                        continue
                    if len(st.rpm_window) >= self.rpm_limit:
                        continue
                    score = st.in_use * 100 + len(st.rpm_window) + st.consecutive_fails * 3
                    if best_score is None or score < best_score:
                        best_score = score
                        best = (idx, st)
                if best is not None:
                    idx, st = best
                    st.in_use += 1
                    st.rpm_window.append(now)
                    st.rpd_count += 1
                    self._cursor = (idx + 1) % n
                    return st.key
            time.sleep(0.5)
        return None

    def release(self, key: str, success: bool, rate_limited: bool = False):
        with self._lock:
            st = self._states.get(key)
            if not st:
                return
            st.in_use = max(0, st.in_use - 1)
            if success:
                st.consecutive_fails = 0
                st.last_ok_at = time.time()
            else:
                st.consecutive_fails += 1
                if rate_limited:
                    penalty = min(65.0 * (1 + st.consecutive_fails * 0.5), 300.0)
                    st.cooldown_until = time.time() + penalty
                elif st.consecutive_fails >= 3:
                    st.cooldown_until = time.time() + 30.0

    def stats(self) -> List[Dict]:
        with self._lock:
            now = time.time()
            out = []
            for k, st in self._states.items():
                st.tick_rpm(now)
                st.tick_rpd(now)
                out.append({
                    "key_tail": k[-6:] if len(k) > 6 else "***",
                    "rpm_used": len(st.rpm_window),
                    "rpd_used": st.rpd_count,
                    "in_use": st.in_use,
                    "cooldown_s": max(0, int(st.cooldown_until - now)),
                    "fails": st.consecutive_fails,
                })
            return out


_POOL: Optional[KeyPool] = None
_POOL_LOCK = threading.Lock()


def get_pool() -> KeyPool:
    """Singleton pool. Keys refreshed from ai_analyzer.get_all_api_keys()."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            try:
                from ai_analyzer import get_all_api_keys
                keys = get_all_api_keys()
            except Exception:
                keys = []
            _POOL = KeyPool(keys)
        return _POOL


def refresh_pool():
    pool = get_pool()
    try:
        from ai_analyzer import get_all_api_keys
        pool.refresh(get_all_api_keys())
    except Exception:
        pass


class JobSlots:
    """Caps concurrent tender-analyst jobs. Keeps Render 512MB from OOM."""
    def __init__(self, max_concurrent: int = 4):
        self.max = max_concurrent
        self._sem = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._active = 0
        self._completed_today = 0
        self._day_start = time.time()

    def acquire(self, timeout: float = 900.0) -> bool:
        ok = self._sem.acquire(timeout=timeout)
        if ok:
            with self._lock:
                self._active += 1
        return ok

    def release(self, ok: bool):
        with self._lock:
            self._active = max(0, self._active - 1)
            if time.time() - self._day_start > 86400:
                self._day_start = time.time()
                self._completed_today = 0
            if ok:
                self._completed_today += 1
        self._sem.release()

    def snapshot(self) -> Dict:
        with self._lock:
            if time.time() - self._day_start > 86400:
                self._day_start = time.time()
                self._completed_today = 0
            return {"active": self._active, "max": self.max,
                    "completed_today": self._completed_today}


_SLOTS: Optional[JobSlots] = None
_SLOTS_LOCK = threading.Lock()


def get_slots() -> JobSlots:
    global _SLOTS
    with _SLOTS_LOCK:
        if _SLOTS is None:
            import os
            mx = int(os.environ.get("ANALYST_MAX_CONCURRENT", "4"))
            _SLOTS = JobSlots(max_concurrent=mx)
        return _SLOTS
