"""Test: EasyOCR in QThread — does it crash?"""
import os, sys, time

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

_result = []
_error = []

class OcrQThread(QThread):
    done = Signal(str)

    def run(self):
        try:
            print("  QThread: starting easyocr import")
            import easyocr
            t = time.time()
            r = easyocr.Reader(["de", "en"], verbose=False)
            elapsed = time.time() - t
            msg = f"OK in {elapsed:.1f}s"
            print(f"  QThread: {msg}")
            self.done.emit(msg)
        except Exception as e:
            msg = f"EXCEPTION: {e}"
            print(f"  QThread: {msg}")
            self.done.emit(msg)

print("=== EasyOCR in QThread (no parent) ===")
worker = OcrQThread()  # NO parent

def on_done(msg):
    _result.append(msg)
    print(f"Signal received: {msg}")
    app.quit()

worker.done.connect(on_done)
worker.start()

# Safety timeout: quit after 60s
QTimer.singleShot(60000, app.quit)

app.exec()

if _result:
    print(f"\nRESULT: {_result[0]}")
    sys.exit(0)
else:
    print("\nRESULT: No signal received (crash or timeout)")
    sys.exit(1)
