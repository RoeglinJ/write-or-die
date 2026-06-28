"""Compatibility wrapper for the Windows executable build."""

from __future__ import annotations

import build_app


if __name__ == "__main__":
    build_app.build("windows")
