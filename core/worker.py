import json
import threading
import time
from typing import Callable, Dict

from sqlalchemy import select

from core.config import settings
from core.database import get_db_session
from core.models import WorkItem


class WorkQueue:
    def __init__(self):
        self.handlers: Dict[str, Callable[[dict], dict]] = {}
        self._stop = threading.Event()
        self._thread = None

    def register(self, work_type: str, handler: Callable[[dict], dict]):
        self.handlers[work_type] = handler

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._process_one()
            except Exception:
                pass
            time.sleep(settings.worker_poll_interval)

    def _process_one(self):
        with get_db_session() as db:
            q = select(WorkItem).where(WorkItem.status == "pending").order_by(WorkItem.id.asc()).limit(1)
            item = db.execute(q).scalars().first()
            if not item:
                return

            item.status = "running"
            item.attempts += 1
            db.flush()

            handler = self.handlers.get(item.work_type)
            if not handler:
                item.status = "error"
                item.error_text = f"no handler for work_type={item.work_type}"
                return

            payload = json.loads(item.payload_json or "{}")
            try:
                result = handler(payload) or {}
                item.result_json = json.dumps(result)
                item.status = "done"
            except Exception as exc:
                item.status = "error"
                item.error_text = str(exc)


work_queue = WorkQueue()
