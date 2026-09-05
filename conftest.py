"""Puts the repo root on sys.path so `import bot` resolves from anywhere.

pytest usually manages this on its own via rootdir insertion, but being
explicit means `python -m pytest tests/test_bot.py` works from any directory.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
