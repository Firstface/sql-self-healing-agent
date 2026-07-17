import errno
import os
import time
from pathlib import Path


class SessionLockTimeout(TimeoutError):
    pass


class SessionLock:
    def __init__(
        self,
        lock_dir: Path | str,
        task_id: str,
        timeout_seconds: float = 5.0,
        stale_seconds: float = 60.0,
        poll_seconds: float = 0.01,
    ) -> None:
        self.lock_dir = Path(lock_dir)
        self.task_id = task_id
        self.timeout_seconds = timeout_seconds
        self.stale_seconds = stale_seconds
        self.poll_seconds = poll_seconds
        self.path = self.lock_dir / f"session_{self._safe_key(task_id)}.lock"
        self._acquired = False

    @staticmethod
    def _safe_key(value: str) -> str:
        return "".join(character if character.isalnum() or character in "._-" else "_" for character in value) or "task"

    def __enter__(self) -> "SessionLock":
        deadline = time.monotonic() + self.timeout_seconds
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
                    lock_file.write(f"{os.getpid()}\n{time.time()}\n")
                    lock_file.flush()
                    os.fsync(lock_file.fileno())
                self._acquired = True
                return self
            except OSError as error:
                if error.errno != errno.EEXIST:
                    raise
                self._remove_if_stale()
                if time.monotonic() >= deadline:
                    raise SessionLockTimeout(f"Timed out acquiring session:{self.task_id}")
                time.sleep(self.poll_seconds)

    def _remove_if_stale(self) -> None:
        try:
            if time.time() - self.path.stat().st_mtime > self.stale_seconds:
                self.path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._acquired:
            self.path.unlink(missing_ok=True)
            self._acquired = False
