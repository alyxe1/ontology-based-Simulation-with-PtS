"""Sequential signed write-ahead log for private simulation branches."""
import os
from pathlib import Path

from .common import canonical

JOURNAL_NAME = "run-journal.jsonl"


class RunJournal:
    def __init__(self, folder):
        self.path = Path(folder) / JOURNAL_NAME
        self.file = self.path.open("xb", buffering=0)
        self.failed = False

    def append(self, record, *, sync):
        line = (canonical(record) + "\n").encode("utf-8")
        if self.file.write(line) != len(line):
            raise OSError("仿真日志写入不完整")
        if sync:
            self.flush()

    def flush(self):
        try:
            os.fsync(self.file.fileno())
        except OSError:
            self.failed = True
            raise

    def close(self):
        self.file.close()
