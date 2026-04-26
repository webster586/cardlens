import os

# Prevent native-library crashes when both Qt (OpenCV) and PyTorch load
# their own OpenMP/MKL runtime in different threads.  Must be set before
# any DLL is loaded, i.e. before any project import.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from src.pokemon_scanner.app import main

if __name__ == "__main__":
    raise SystemExit(main())
