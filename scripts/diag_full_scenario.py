"""Test: EasyOCR in QThread MIT echtem MainWindow + laufender Splash-Animation.
Simuliert exakt den App-Zustand zur Crash-Zeit."""
import os, sys, time

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout
from PySide6.QtGui import QFont

app = QApplication(sys.argv)

# ── Fake Splash with animating timer (like real splash) ──
class FakeSplash(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Loading…")
        self.resize(400, 200)
        self._dots = 0
        layout = QVBoxLayout(self)
        self._label = QLabel("OCR-Modell wird geladen")
        self._label.setFont(QFont("Arial", 14))
        layout.addWidget(self._label)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._dots = (self._dots + 1) % 4
        self._label.setText("OCR-Modell wird geladen" + "." * self._dots)

    def finish_loading(self):
        self._timer.stop()
        QTimer.singleShot(600, self.close)


# ── Fake MainWindow (parent for QThread) ──
class FakeMain(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pokemon Card Scanner")
        self.resize(1000, 700)
        lbl = QLabel("Main window — OCR loading in background…")
        self.setCentralWidget(lbl)

        # Start OCR warmup worker WITH self as parent (exact same as real app)
        self._worker = OcrQThread(self)
        self._worker.done.connect(self._on_ocr_done)
        self._worker.start()
        print("  MainWindow: worker started")

    def _on_ocr_done(self, msg):
        print(f"  MainWindow: OCR done signal: {msg}")
        splash.finish_loading()
        QTimer.singleShot(1000, app.quit)


class OcrQThread(QThread):
    done = Signal(str)

    def run(self):
        try:
            print("  OcrQThread.run(): importing easyocr…")
            import easyocr
            t = time.time()
            r = easyocr.Reader(["de", "en"], verbose=False)
            elapsed = time.time() - t
            msg = f"OK in {elapsed:.1f}s"
            print(f"  OcrQThread.run(): {msg}")
            self.done.emit(msg)
        except Exception as e:
            msg = f"EXCEPTION: {e}"
            print(f"  OcrQThread.run(): {msg}")
            self.done.emit(msg)


print("=== EasyOCR in QThread WITH MainWindow parent + Splash animation ===")

splash = FakeSplash()
splash.show()
app.processEvents()

# Pre-load torch in main thread (same as current app.py)
try:
    import torch
    torch.set_num_threads(1)
    print(f"torch preloaded: {torch.__version__}")
except Exception as e:
    print(f"torch preload failed: {e}")

window = FakeMain()
window.show()

# Safety timeout
QTimer.singleShot(120000, app.quit)

rc = app.exec()
print(f"\napp.exec() returned: {rc}")
sys.exit(rc)
