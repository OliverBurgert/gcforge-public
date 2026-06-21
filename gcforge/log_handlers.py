"""
Windows-compatible rotating file handler.

Python's standard RotatingFileHandler uses os.rename which fails on Windows
when another process holds a read lock on the file (e.g. the log viewer reading
gcforge.log).  This handler uses copy-then-truncate instead, which works even
when the file is open for reading.
"""
import logging.handlers
import os
import shutil


class CopyTruncateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        # Rotate numbered backups: .4 → gone, .3 → .4, …, .1 → .2
        for i in range(self.backupCount - 1, 0, -1):
            src = f"{self.baseFilename}.{i}"
            dst = f"{self.baseFilename}.{i + 1}"
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.copy2(src, dst)
                os.remove(src)

        # Copy current log to .1, then truncate in-place (don't rename)
        dst1 = f"{self.baseFilename}.1"
        if os.path.exists(dst1):
            os.remove(dst1)
        if os.path.exists(self.baseFilename):
            shutil.copy2(self.baseFilename, dst1)
            with open(self.baseFilename, "w", encoding=self.encoding or "utf-8"):
                pass  # truncate

        if not self.delay:
            self.stream = self._open()
