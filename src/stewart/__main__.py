"""python -m stewart — `stewart` 명령과 같다(Windows 런처 등이 이것으로 띄운다)."""
import sys

from .cli import main

sys.exit(main())
