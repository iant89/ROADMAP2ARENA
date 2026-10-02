"""Minimal runner (pytest not installed): runs test_* functions in file order, retries a failure once."""
import importlib.util, sys, time, traceback
spec = importlib.util.spec_from_file_location("t", "/app/backend/tests/test_backend_api.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
names = [n for n in m.__dict__ if n.startswith("test_") and callable(m.__dict__[n])]
only = sys.argv[1:]
passed, failed = 0, []
for n in names:
    if only and n not in only and n != "test_zz_cleanup":
        continue
    for attempt in (1, 2):
        try:
            t = time.time(); m.__dict__[n](); print(f"PASS {n} ({time.time()-t:.1f}s)" + (" [on retry]" if attempt == 2 else "")); passed += 1; break
        except Exception as e:
            if attempt == 1 and n not in ("test_zz_cleanup",):
                print(f"retry {n}: {type(e).__name__}: {e}"); continue
            print(f"FAIL {n}: {type(e).__name__}: {e}"); traceback.print_exc(limit=2); failed.append(n)
print(f"\n{passed} passed, {len(failed)} failed: {failed}")
sys.exit(1 if failed else 0)
