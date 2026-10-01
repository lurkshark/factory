import os
import traceback

try:
    from .cli import main
except Exception as exc:
    if os.environ.get("SPECFLOW_DEBUG") == "1":
        traceback.print_exc()
    print(f"ERROR tooling spec: {type(exc).__name__}: {str(exc).replace(chr(10), ' ')}")
    raise SystemExit(2)

raise SystemExit(main())
