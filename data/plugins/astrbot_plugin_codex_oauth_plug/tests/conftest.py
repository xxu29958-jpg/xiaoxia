"""Load the maintained provider package without replacing AstrBot modules."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
