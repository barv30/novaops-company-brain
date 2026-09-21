"""Settings, paths, and the pinned clock.

One module owns every value that varies by environment, so a reader looking for
"where does this number come from" has one place to look.

The clock is here rather than in a utility module on purpose. Every case in the
evaluation set is run against `2026-07-01`, and renewal windows, start dates and
"currently blocked" all depend on it. A stray `date.today()` anywhere in the system
makes a run unreproducible in a way that is invisible until the answers drift.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


# 1. THE PINNED CLOCK --------------------------------------------------------

# The dataset's own as_of_date is 2026-07-02; the specification pins evaluation to
# the day before. Never call date.today() anywhere in this system — take it from here.
AS_OF: date = date.fromisoformat(os.environ.get("NOVAOPS_AS_OF", "2026-07-01"))


def today() -> date:
    """The current date as this system understands it. The only source of 'now'."""

    return AS_OF


# 2. PATHS -------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

DATASET_DIR = REPO_ROOT / "novaops-enterprise-agent-dataset"
DOCUMENTS_DIR = DATASET_DIR / "documents"
DATABASE_DIR = DATASET_DIR / "database"

SCHEMA_SQL = DATABASE_DIR / "schema.sql"
SEED_SQL = DATABASE_DIR / "seed.sql"
SEED_JSON = DATABASE_DIR / "seed.json"

GOLDEN_DATASETS = REPO_ROOT / "GOLDEN-DATASETS.json"
EVALUATION_INPUTS = REPO_ROOT / "EVALUATION-INPUTS.yaml"

# Build artefacts. Git-ignored; recreated by scripts/reseed.py and scripts/ingest.py.
VAR_DIR = REPO_ROOT / "var"
BUSINESS_DB = Path(os.environ.get("NOVAOPS_DB", VAR_DIR / "novaops.db"))
CHECKPOINT_DB = Path(os.environ.get("NOVAOPS_CHECKPOINT_DB", VAR_DIR / "checkpoints.db"))
INDEX_DIR = Path(os.environ.get("NOVAOPS_INDEX_DIR", VAR_DIR / "faiss"))


# 3. SERVICES ----------------------------------------------------------------

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:9877/mcp")
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "9877"))

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
BEDROCK_EMBED_MODEL_ID = os.environ.get(
    "BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"
)
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


# 4. BEHAVIOR LIMITS ---------------------------------------------------------

# Bounded retrieval refinement. When evidence is missing, allow at most this many
# refined rounds and then answer with what is available, saying what is missing.
# "Search again until something turns up" has no natural stopping point, and that is
# where both latency and cost go.
MAX_RETRIEVAL_REFINEMENTS = 1

# How many documents a single retrieval round returns before reranking.
RETRIEVAL_TOP_K = int(os.environ.get("NOVAOPS_TOP_K", "8"))


def ensure_var_dir() -> Path:
    """Create the build-artefact directory if it does not exist yet."""

    VAR_DIR.mkdir(parents=True, exist_ok=True)
    return VAR_DIR
