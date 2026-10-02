import sys

from PyQt5.QtCore import QEventLoop, QTimer
from PyQt5.QtWidgets import QApplication

from ui.TaskRunner import TaskRunner


app = QApplication.instance() or QApplication(sys.argv)


def test_result_callback_failure_is_reported_without_aborting_qt():
    runner = TaskRunner()
    loop = QEventLoop()
    failures = []
    runner.task_failed.connect(lambda description, error: failures.append((description, error)))
    runner.task_finished.connect(lambda _description: loop.quit())

    def broken_callback(_result):
        raise ValueError("callback failed")

    runner.run(lambda: 1, description="test task", on_result=broken_callback)
    QTimer.singleShot(2000, loop.quit)
    loop.exec_()

    assert len(failures) == 1
    assert failures[0][0] == "test task"
    assert isinstance(failures[0][1], ValueError)
    assert not runner._active_workers
