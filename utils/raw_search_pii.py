import os
from typing import Dict, Any, List

import pandas as pd


# Filenames for raw PII communications
PII_FILENAME = "customer_communication_logs_100_rows.csv"

# Try a few common locations so you don't have to hardcode paths
PII_CANDIDATES = [
    PII_FILENAME,
    os.path.join("data", PII_FILENAME),
    os.path.join("tests_data", PII_FILENAME),
]


def _load_raw_pii() -> pd.DataFrame:
    """Load raw PII communications CSV from the first existing candidate path."""
    for path in PII_CANDIDATES:
        if os.path.exists(path):
            return pd.read_csv(path)
    # If nothing found, return empty DF with expected columns so we don't crash
    return pd.DataFrame(columns=["message_id", "channel", "text"])


def raw_search_pii(query: str, limit: int = 20) -> Dict[str, Any]:
    """
    Naive PII search directly over raw chat logs (no tagging).

    - Filters rows where `text` contains the query (case-insensitive).
    - Returns minimal metadata so the writer can still format something.
    - Marked as `approximate=True` so we can distinguish this from tagged tools.
    """
    df = _load_raw_pii()
    if df.empty or "text" not in df.columns:
        return {
            "tool": "raw_search_pii",
            "query": query,
            "approximate": True,
            "hits": [],
            "count": 0,
            "note": "Raw PII CSV not found or missing 'text' column.",
        }

    mask = df["text"].astype(str).str.contains(query, case=False, na=False)
    hits_df = df[mask].head(limit)

    hits: List[Dict[str, Any]] = []
    for _, row in hits_df.iterrows():
        hits.append(
            {
                # These may or may not exist in the raw CSV; .get-style access is safe
                "message_id": row.get("message_id"),
                "channel": row.get("channel"),
                # Raw text only – no masking / entities / risk here
                "text": row.get("text"),
            }
        )

    return {
        "tool": "raw_search_pii",
        "query": query,
        "approximate": True,
        "hits": hits,
        "count": len(hits),
    }
