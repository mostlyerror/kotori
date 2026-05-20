import os
from pathlib import Path

DB_PATH = os.environ.get("KOTORI_DB", str(Path.home() / ".kotori" / "kotori.db"))
TRADIER_API_KEY = os.environ.get("TRADIER_API_KEY", "")
TRADIER_ACCOUNT_ID = os.environ.get("TRADIER_ACCOUNT_ID", "")
TRADIER_ENV = os.environ.get("TRADIER_ENV", "sandbox")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TRADIER_BASE = (
    "https://sandbox.tradier.com/v1"
    if TRADIER_ENV == "sandbox"
    else "https://api.tradier.com/v1"
)
