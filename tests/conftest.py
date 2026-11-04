"""Pytest configuration.

Tests require 'ayon_core' to be importable, e.g. run them with the
ayon-core development environment and its 'client' folder in PYTHONPATH.
"""
import sys
from pathlib import Path

client_path = Path(__file__).resolve().parent.parent / "client"

sys.path.append(str(client_path))
