# PyInstaller runtime hook — runs before any app import.
# Tells EasyOCR to look for model files inside the bundle (dist/model/)
# instead of the user's home directory.
import os
import sys

if getattr(sys, "frozen", False):
    os.environ["EASYOCR_MODULE_PATH"] = sys._MEIPASS
