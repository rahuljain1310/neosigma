#!/usr/bin/env python3
"""Dump the OpenAPI spec to openapi.json without running the server."""

import json
from pathlib import Path

from app.main import app

if __name__ == "__main__":
    out = Path("openapi.json")
    out.write_text(json.dumps(app.openapi(), indent=2))
    print(f"Wrote {out}")
