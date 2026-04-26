"""Crash root-cause diagnostic — runs three scenarios and reports DLL/thread info."""
import os, sys, threading, time

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

# ── Step 1: Which OpenMP DLLs are loaded after Qt+cv2 vs torch? ───────────
print("=== STEP 1: DLL audit ===")
try:
    import psutil
    proc = psutil.Process(os.getpid())

    from PySide6.QtWidgets import QApplication
    import cv2
    app = QApplication(sys.argv)

    dlls_after_qt = sorted({
        m.path for m in proc.memory_maps()
        if any(k in m.path.lower() for k in ("omp", "mkl", "blas", "openblas"))
    })
    print(f"OpenMP/BLAS DLLs after Qt+cv2 ({len(dlls_after_qt)}):")
    for d in dlls_after_qt: print("  ", d)

    import torch
    dlls_after_torch = sorted({
        m.path for m in proc.memory_maps()
        if any(k in m.path.lower() for k in ("omp", "mkl", "blas", "openblas"))
    })
    new_dlls = set(dlls_after_torch) - set(dlls_after_qt)
    print(f"New DLLs after torch ({len(new_dlls)}):")
    for d in new_dlls: print("  ", d)
    print(f"torch version: {torch.__version__}")
    print(f"torch.get_num_threads: {torch.get_num_threads()}")

except ImportError as e:
    print(f"psutil not available, skipping DLL audit: {e}")
    from PySide6.QtWidgets import QApplication
    import cv2, torch
    app = QApplication(sys.argv)
    print(f"torch version: {torch.__version__}")

# ── Step 2: Does EasyOCR load OK in main thread? ──────────────────────────
print("\n=== STEP 2: EasyOCR in main thread ===")
try:
    import easyocr
    t = time.time()
    r = easyocr.Reader(["de", "en"], verbose=False)
    print(f"OK in {time.time()-t:.1f}s — reader type: {type(r)}")
    _reader_ok = True
except Exception as e:
    print(f"FAILED: {e}")
    _reader_ok = False

if not _reader_ok:
    print("Main-thread load failed → likely model-file or package issue, not threading.")
    sys.exit(1)

# ── Step 3: Does EasyOCR load OK in a plain Python thread? ────────────────
print("\n=== STEP 3: EasyOCR in threading.Thread (NOT QThread) ===")
_thread_result = []
_thread_exc = []

def _load_in_thread():
    try:
        import easyocr
        r2 = easyocr.Reader(["de", "en"], verbose=False)
        _thread_result.append(r2)
        print(f"  threading.Thread: OK — {type(r2)}")
    except Exception as e:
        _thread_exc.append(e)
        print(f"  threading.Thread: FAILED — {e}")

t_thread = threading.Thread(target=_load_in_thread, daemon=True)
t_thread.start()
t_thread.join(timeout=60)
if t_thread.is_alive():
    print("  threading.Thread: TIMED OUT (still running after 60s)")
elif _thread_exc:
    print(f"  threading.Thread: EXCEPTION: {_thread_exc[0]}")
else:
    print("  threading.Thread: SUCCESS")

# ── Step 4: Report torch internals ────────────────────────────────────────
print("\n=== STEP 4: torch internals ===")
try:
    import torch
    print(f"  torch.__version__: {torch.__version__}")
    print(f"  torch.backends.openmp.is_available(): {torch.backends.openmp.is_available()}")
    print(f"  torch.get_num_threads(): {torch.get_num_threads()}")
    print(f"  torch.get_num_interop_threads(): {torch.get_num_interop_threads()}")
except Exception as e:
    print(f"  torch internals error: {e}")

print("\n=== DONE ===")
sys.exit(0)
