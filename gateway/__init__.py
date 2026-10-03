"""Local integration for the separately maintained arena2api gateway."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_DIR = REPO_ROOT / "services" / "arena2api"
UPSTREAM_URL = "https://github.com/flay-o/arena2api"
UPSTREAM_REVISION = "259e27c2a96c8203cfe6d67140490b0db9f91543"
