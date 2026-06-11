import sys
from pathlib import Path

# Make scripts/ importable in tests
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
