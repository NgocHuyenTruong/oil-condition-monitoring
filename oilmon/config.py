"""Shared settings: paths, API details, the test catalogue and validation rules."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"
POWERBI_DIR = OUTPUT_DIR / "powerbi"
REPORT_DIR = OUTPUT_DIR / "reports"
STATE_FILE = OUTPUT_DIR / "ingest_state.json"

# Mock lab API (stand-in for the commercial laboratory's REST API).
API_HOST = "127.0.0.1"
API_PORT = 8765
API_BASE_URL = os.environ.get("LAB_API_URL", f"http://{API_HOST}:{API_PORT}")
API_KEY = os.environ.get("LAB_API_KEY", "demo-key-not-a-secret")

# Test code -> (description, standard unit). Order is the order used in reports.
TESTS: dict[str, tuple[str, str]] = {
    "Fe": ("Iron", "ppm"),
    "Cu": ("Copper", "ppm"),
    "Pb": ("Lead", "ppm"),
    "Cr": ("Chromium", "ppm"),
    "Al": ("Aluminium", "ppm"),
    "Sn": ("Tin", "ppm"),
    "Si": ("Silicon (dirt)", "ppm"),
    "Na": ("Sodium", "ppm"),
    "K": ("Potassium", "ppm"),
    "V100": ("Viscosity @100C", "cSt"),
    "SOOT": ("Soot", "%"),
    "WATER": ("Water", "%"),
    "FUEL": ("Fuel dilution", "%"),
    "TBN": ("Total base number", "mgKOH/g"),
}

# Tests that build up in the oil while it is in service. A 500 h sample naturally
# reads about twice a 250 h sample, so these are normalised to 250 oil hours
# before comparing samples with each other.
ACCUMULATIVE = ["Fe", "Cu", "Pb", "Cr", "Al", "Sn", "Si", "Na", "K", "SOOT"]
NORMALISE_TO_HOURS = 250
NORMALISE_VALID_HOURS = (100, 700)

# Unit labels that mean the same thing as the standard unit.
UNIT_ALIASES = {"mg/kg": "ppm", "mm2/s": "cSt", "mm²/s": "cSt", "wt%": "%", "% vol": "%"}
# (test, reported unit) -> factor to convert into the standard unit.
UNIT_CONVERSIONS = {("WATER", "ppm"): 1e-4}

# Values outside these ranges are physically implausible for engine oil.
PLAUSIBLE_RANGE = {
    "V100": (2.0, 40.0),
    "SOOT": (0.0, 15.0),
    "WATER": (0.0, 10.0),
    "FUEL": (0.0, 30.0),
    "TBN": (0.0, 20.0),
}
PLAUSIBLE_METAL_PPM = (0.0, 5000.0)

# A sample normally reaches the lab within a few days of being taken.
MAX_TRANSIT_DAYS = 21

# Anomaly detection (see oilmon/analyse.py).
ANOMALY_Z = 3.5
ANOMALY_MIN_HISTORY = 4
ANOMALY_WINDOW = 8
# Smallest change that counts, so tiny values (e.g. Cr 1 -> 2 ppm) are not flagged.
ANOMALY_SCALE_FLOOR = {
    "Fe": 2.0, "Cu": 1.0, "Pb": 1.0, "Cr": 0.5, "Al": 1.0, "Sn": 0.5,
    "Si": 1.0, "Na": 1.5, "K": 1.0, "SOOT": 0.15,
    "V100": 0.3, "WATER": 0.02, "FUEL": 0.3,
}
TREND_POINTS = 6
TREND_MIN_R2 = 0.8
TREND_MIN_RISE_FLOORS = 4  # total rise across the window, in units of the scale floor
OVERDUE_DAYS = 30
