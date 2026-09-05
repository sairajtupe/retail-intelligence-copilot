"""Application configuration."""
from __future__ import annotations
import os
from pathlib import Path
from logging import getLogger, INFO, StreamHandler, Formatter

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "documents"
INDEX_DIR = BASE_DIR / "index"
FRONTEND_DIR = BASE_DIR / "frontend"
DB_PATH = DATA_DIR / "retail.db"

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL: str = "gemini-3.5-flash-lite"
EMBED_MODEL: str = "gemini-embedding-001"
GEMINI_AVAILABLE: bool = bool(GEMINI_API_KEY)

PORT: int = 8000
APP_NAME: str = "Retail Intelligence Copilot"
APP_TAGLINE: str = "AI-powered sales and inventory decisions grounded in your store data"
CURRENCY_SYMBOL: str = "₹"

# ---------------------------------------------------------------------------
# Central analytical thresholds. The LLM never decides these.
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "min_history_days": 21,            # EDG-01 / DEF-16
    "ads_window_days": 30,
    "trend_window_days": 7,
    "spike_z_threshold": 2.0,          # ANM-D-01 standard deviations
    "drop_threshold_pct": 40.0,        # ANM-D-02
    "drop_severe_pct": 60.0,           # ANM-D-06
    "promo_spike_override_pct": 500.0, # ANM-D-04
    "slow_moving_stock_pct": 0.10,     # SLM-01 / DEF-12
    "slow_moving_cover_days": 90.0,
    "overstock_cover_days": 90.0,      # OVR-01 / DEF-14
    "dead_stock_zero_days": 30,
    "safety_stock_ratio": 0.30,        # DEF-05
    "safety_stock_floor": 5.0,
    "max_stock_cover_target_mult": 1.5,
    "transfer_excess_mult": 1.0,       # TRF-01: source cover > dest cover + 100%
    "trend_acceleration_pct": 20.0,    # STK-02
}

# Copilot response confidence rules (REC-05)
CONFIDENCE = {
    "HIGH": 0.8,
    "MEDIUM": 0.6,
    "LOW": 0.4,
}

log = getLogger("retail_copilot")
if not log.handlers:
    log.setLevel(INFO)
    _h = StreamHandler()
    _h.setFormatter(Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(_h)