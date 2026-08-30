"""Load the browser front-end HTML template."""

import os

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_INDEX_PATH = os.path.join(_TEMPLATE_DIR, "index.html")


def load_index_html() -> str:
    with open(_INDEX_PATH, "r", encoding="utf-8") as f:
        return f.read()
