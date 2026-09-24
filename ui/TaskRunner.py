from __future__ import annotations

import traceback
from typing import Any, Callable, Optional

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class _WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(object)
    finished = pyqtSignal()


class _Worker(QRunnable):
    def __init__(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()

    def run(self) -> None:  # noqa: D401 - Qt entrypoint
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - propagate to UI thread
            traceback.print_exc()
            try:
                self.signals.error.emit(exc)
            except RuntimeError:
                pass  # The application closed while this worker was finishing.
        else:
            try:
                self.signals.result.emit(result)
            except RuntimeError:
                pass
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass


class TaskRunner(QObject):
    task_started = pyqtSignal(str)
    task_finished = pyqtSignal(str)
    task_failed = pyqtSignal(str, object)
    _running_workers: set[_Worker] = set()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._active_workers: set[_Worker] = set()

    def run(
        self,
        fn: Callable,
        *args: Any,
        description: str = "",
        on_result: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> None:
        worker = _Worker(fn, *args, **kwargs)
        self._active_workers.add(worker)
        self._running_workers.add(worker)

        if description:
            self.task_started.emit(description)

        if on_result is not None:
            def _handle_result(result: Any) -> None:
                try:
                    on_result(result)
                except Exception as exc:  # noqa: BLE001 - Qt slots must not abort the process
                    traceback.print_exc()
                    if description:
                        self.task_failed.emit(description, exc)

            worker.signals.result.connect(_handle_result)

        def _handle_error(exc: Exception) -> None:
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception:  # noqa: BLE001 - keep callback errors in the UI thread
                    traceback.print_exc()
            if description:
                self.task_failed.emit(description, exc)

        worker.signals.error.connect(_handle_error)

        def _handle_finished() -> None:
            try:
                if on_finished is not None:
                    try:
                        on_finished()
                    except Exception:  # noqa: BLE001 - Qt slots must not abort the process
                        traceback.print_exc()
                if description:
                    self.task_finished.emit(description)
            finally:
                self._active_workers.discard(worker)
                self._running_workers.discard(worker)

        worker.signals.finished.connect(_handle_finished)

        self._pool.start(worker)
