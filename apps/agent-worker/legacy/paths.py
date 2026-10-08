"""Writable project paths remain beside the EXE, outside its extraction directory."""
from pathlib import Path
import os
import sys

APP = Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
ROOT = Path(os.environ.get('CHARACTER_IMAGE_ROOT', str(APP.parent))).resolve()
