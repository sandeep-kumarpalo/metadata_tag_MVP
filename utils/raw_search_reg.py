import os
from typing import Dict, Any, List

import pandas as pd


REG_FILENAME = "regulatory_paragraphs_45_rows.csv"

REG_CANDIDATES = [
    REG_FILENAME,
    os.path.join("data", REG_FILENAME),
    os.path.join("tests_data", REG_FILENAME),
]


def _load_raw_reg() -> pd.DataFrame:
    """Load raw regulatory paragraphs CSV from the first existing candidate path."""
    for path in REG_CANDIDATES:
        if os.path.exists(path):
            return pd.read_csv(path)
    # Empty DF with expected columns
    return pd.DataFrame(
        columns=["paragraph_id", "source_document", "regulation", "article", "paragraph_text"]
    )


def raw_search_reg(query: str, limit: int = 20) -> Dict[str, Any]:
    """
    Naive regulatory search over raw paragraphs.

    - Filters rows where `paragraph_text` contains the query (case-insensitive).
    - Returns id + source + regulation + article + paragraph_text.
    - No owner / business_unit / deadline tagging here.
    """
    df = _load_raw_reg()
    if df.empty or "paragraph_text" not in df.columns:
        return {
            "tool": "raw_search_reg",
            "query": query,
            "approximate": True,
            "matches": [],
            "count": 0,
            "note": "Raw REG CSV not found or missing 'paragraph_text' column.",
        }

    mask = df["paragraph_text"].astype(str).str.contains(query, case=False, na=False)
    hits_df = df[mask].head(limit)

    matches: List[Dict[str, Any]] = []
    for _, row in hits_df.iterrows():
        matches.append(
            {
                "paragraph_id": row.get("paragraph_id"),
                "source_document": row.get("source_document"),
                "regulation": row.get("regulation"),
                "article": row.get("article"),
                "paragraph_text": row.get("paragraph_text"),
            }
        )

    return {
        "tool": "raw_search_reg",
        "query": query,
        "approximate": True,
        "matches": matches,
        "count": len(matches),
    }
