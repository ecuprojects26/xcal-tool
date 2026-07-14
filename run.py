"""Entry point: launch the xcaltool GUI.

Run with:  python run.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from xcaltool.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
