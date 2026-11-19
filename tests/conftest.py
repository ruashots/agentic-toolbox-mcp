"""Shared setup: import the gateway and watcher modules from gateway/."""
import sys
from pathlib import Path

GATEWAY = Path(__file__).resolve().parent.parent / "gateway"
sys.path.insert(0, str(GATEWAY))
