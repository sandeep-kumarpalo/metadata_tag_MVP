import os
from typing import Dict, Any, List

import pandas as pd


AML_FILENAME = "transaction_narratives_120_rows.csv"

AML_CANDIDATES = [
    AML_FILENAME,
    os.path.join("data", AML_FILENAME),
    os.path.join("tests_data", AML_FILENAME),
]


def _load_raw_aml() -> pd.DataFrame:
    """Load raw AML transactions CSV from the first existing candidate path."""
    for path in AML_CANDIDATES:
        if os.path.exists(path):
            return pd.read_csv(path)
    # Empty DF with expected core columns
    return pd.DataFrame(columns=["transaction_id", "amount_sgd", "date", "narrative"])


def raw_search_aml(query: str, limit: int = 20) -> Dict[str, Any]:
    """
    Naive AML search over raw transaction narratives.

    - Filters rows where `narrative` contains the query (case-insensitive).
    - Returns id + amount + narrative only (no tagged risk_score/aml_tags).
    - Marked as `approximate=True` so the writer knows this is raw.
    """
    df = _load_raw_aml()
    if df.empty or "narrative" not in df.columns:
        return {
            "tool": "raw_search_aml",
            "query": query,
            "approximate": True,
            "matches": [],
            "count": 0,
            "note": "Raw AML CSV not found or missing 'narrative' column.",
        }

    mask = df["narrative"].astype(str).str.contains(query, case=False, na=False)
    hits_df = df[mask].head(limit)

    matches: List[Dict[str, Any]] = []
    for _, row in hits_df.iterrows():
        matches.append(
            {
                "transaction_id": row.get("transaction_id"),
                "amount_sgd": row.get("amount_sgd"),
                "date": row.get("date"),
                # Raw narrative text – no masking or aml_tags/risk_score
                "narrative": row.get("narrative"),
            }
        )

    return {
        "tool": "raw_search_aml",
        "query": query,
        "approximate": True,
        "matches": matches,
        "count": len(matches),
    }
