import sys
from pathlib import Path

# Make `import app` work no matter where pytest is invoked from.
BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
