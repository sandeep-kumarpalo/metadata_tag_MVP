import os
import json

import pandas as pd
import numpy as np
import faiss
from openai import AzureOpenAI
from dotenv import load_dotenv

# ---------------------------------------------------------
# Local safe_load (no dependency on tagging_wrappers)
# ---------------------------------------------------------


def safe_load(path: str) -> pd.DataFrame:
    """
    Safe CSV loader used by semantic layer queries.
    Returns empty DataFrame if file does not exist.
    """
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame()


# ---------------------------------------------------------
# Azure OpenAI client (used for embeddings)
# ---------------------------------------------------------

load_dotenv()

client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("OPENAI_API_KEY"),
    api_version=os.getenv("OPENAI_API_VERSION"),
)

# Embedding deployment (must exist in your Azure OpenAI resource)
# Example in .env: EMBEDDING_DEPLOYMENT=text-embedding-ada-002
EMBEDDING_DEPLOYMENT = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")


# ---------------------------------------------------------
# Azure embedding helper (robust, per-record)
# ---------------------------------------------------------


def embed_texts_azure(texts: list[str]) -> np.ndarray:
    """
    Embed text using your Azure OpenAI embedding deployment.

    Uses EMBEDDING_DEPLOYMENT (e.g. 'text-embedding-ada-002').

    Returns:
        np.ndarray of shape (len(successful_texts), dim) in float32
        or an empty (0, 0) array if everything fails.

    This function is defensive:
      - Cleans each text.
      - Calls the Azure embeddings API per record (so one bad record
        doesn't break the whole batch).
      - Retries a few times before skipping a record.
    """
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    vectors: list[np.ndarray] = []

    for idx, raw in enumerate(texts):
        # Basic cleaning to avoid weird control characters
        cleaned = (raw or "").replace("\x00", " ").strip()
        if not cleaned:
            # Nothing to embed
            continue

        success = False
        for attempt in range(3):
            try:
                response = client.embeddings.create(
                    model=EMBEDDING_DEPLOYMENT,
                    input=[cleaned],
                )
                emb = np.array(response.data[0].embedding, dtype=np.float32)
                vectors.append(emb)
                success = True
                break
            except Exception as e:
                # Printed as debug; does not raise to keep rest running
                print(
                    f"Azure embedding error at index {idx}, attempt {attempt + 1}/3:",
                    e,
                )
        if not success:
            print(f"⚠️ Azure embedding permanently failed at index {idx}")

    if not vectors:
        return np.zeros((0, 0), dtype=np.float32)

    return np.vstack(vectors)


# ======================================================================
# 1. dbt-core style semantic layer (metrics only, no vectors)
# ======================================================================


def build_dbt_core_layer(tagged_data: dict) -> dict:
    """
    Lightweight dbt-core-style metrics over tagged AML + PII + REG DataFrames.

    tagged_data: {
        'aml': DataFrame with columns ['risk_score', ...]
        'pii': DataFrame with columns ['risk_flag', ...]
        'reg': DataFrame with columns ['owner', 'source_document', 'risk_type', ...]
    }

    Returns:
      {
        'metrics': {
            'aml_high_risk_count': int,
            'avg_risk_score': float,
            'pii_critical_count': int,
            'reg_total_paragraphs': int,
            'reg_owner_breakdown': dict,
            'reg_doc_breakdown': dict,
            'reg_risk_type_breakdown': dict,
          },
        'status': 'dbt Core complete'
      }
    """
    try:
        metrics: dict = {}

        # ---------------- AML metrics ----------------
        if (
            "aml" in tagged_data
            and tagged_data["aml"] is not None
            and not tagged_data["aml"].empty
        ):
            df_aml = tagged_data["aml"]
            if "risk_score" in df_aml.columns:
                high_risk = df_aml[df_aml["risk_score"] > 8]
                metrics["aml_high_risk_count"] = int(len(high_risk))
                metrics["avg_risk_score"] = float(df_aml["risk_score"].mean())

        # ---------------- PII metrics ----------------
        if (
            "pii" in tagged_data
            and tagged_data["pii"] is not None
            and not tagged_data["pii"].empty
        ):
            df_pii = tagged_data["pii"]
            if "risk_flag" in df_pii.columns:
                metrics["pii_critical_count"] = int(
                    (df_pii["risk_flag"].astype(str).str.lower() == "critical").sum()
                )

        # ---------------- REG metrics ----------------
        if (
            "reg" in tagged_data
            and tagged_data["reg"] is not None
            and not tagged_data["reg"].empty
        ):
            df_reg = tagged_data["reg"]

            # Total obligations
            metrics["reg_total_paragraphs"] = int(len(df_reg))

            # Owner breakdown
            if "owner" in df_reg.columns:
                metrics["reg_owner_breakdown"] = (
                    df_reg["owner"].astype(str).value_counts().to_dict()
                )

            # Document breakdown
            if "source_document" in df_reg.columns:
                metrics["reg_doc_breakdown"] = (
                    df_reg["source_document"].astype(str).value_counts().to_dict()
                )

            # Risk-type breakdown
            if "risk_type" in df_reg.columns:
                metrics["reg_risk_type_breakdown"] = (
                    df_reg["risk_type"].astype(str).value_counts().to_dict()
                )

        return {"metrics": metrics, "status": "dbt Core complete"}

    except Exception as e:
        return {"error": str(e)}



# ======================================================================
# 2. Hybrid dbt + FAISS vector layer (using Azure embeddings)
# ======================================================================


def build_dbt_faiss_hybrid_layer(tagged_data: dict) -> dict:
    """
    Hybrid semantic layer:

    - Reuses dbt-core metrics (AML + PII + Reg)
    - Builds a FAISS index over AML masked_narrative embeddings
      using Azure OpenAI embeddings (no HuggingFace needed).
    """
    try:
        # First, compute all dbt-core metrics (including Reg)
        base = build_dbt_core_layer(tagged_data)
        metrics = dict(base.get("metrics", {}))

        # If we don't have AML data, we can't build a FAISS index
        if (
            "aml" not in tagged_data
            or tagged_data["aml"] is None
            or tagged_data["aml"].empty
        ):
            return {"metrics": metrics, "status": "Hybrid complete"}

        df_aml = tagged_data["aml"]

        # Prefer masked_narrative; fallback to original_narrative
        if "masked_narrative" in df_aml.columns:
            texts = df_aml["masked_narrative"].fillna("").tolist()
            print("FAISS DEBUG → narrative column used: masked_narrative")
        else:
            texts = df_aml["original_narrative"].fillna("").tolist()
            print("FAISS DEBUG → narrative column used: original_narrative")

        print(f"FAISS DEBUG → number of texts: {len(texts)}")

        if not texts:
            return {"metrics": metrics, "status": "Hybrid complete"}

        # Embed with Azure (defensive, per-record)
        embeddings = embed_texts_azure(texts)
        if embeddings.size == 0:
            return {
                "metrics": metrics,
                "status": "Hybrid complete (embedding failed)",
            }

        # Build FAISS index
        d = embeddings.shape[1]
        index = faiss.IndexFlatL2(d)
        index.add(embeddings.astype(np.float32))

        os.makedirs("outputs", exist_ok=True)
        faiss.write_index(index, os.path.join("outputs", "faiss_index.index"))
        metrics["faiss_size"] = int(index.ntotal)

        return {"metrics": metrics, "status": "Hybrid complete"}
    except Exception as e:
        return {"error": str(e)}


# ======================================================================
# 3. Simple query helpers for agents
# ======================================================================


def query_semantic_layer(query: str) -> dict:
    """
    Simple semantic-layer query used by agents.

    For demo purposes we ignore the query text and just return
    the dbt-core metrics computed from whatever tagged_aml /
    tagged_pii / tagged_reg we have on disk.
    """
    try:
        tagged_data = {
            "aml": safe_load(os.path.join("outputs", "tagged_aml.csv")),
            "pii": safe_load(os.path.join("outputs", "tagged_pii.csv")),
            "reg": safe_load(os.path.join("outputs", "tagged_regulatory.csv")),
        }
        layer = build_dbt_core_layer(tagged_data)
        return layer.get("metrics", {})
    except Exception as e:
        return {"error": str(e)}


def query_vector_layer(query: str) -> dict:
    """
    Simple vector-layer query used by agents.

    - Loads FAISS index from disk (if present)
    - Encodes the query with the same Azure embedding model
    - Returns top-3 nearest neighbor indices & distances
    """
    try:
        # Embed query with Azure
        emb = embed_texts_azure([query])
        if emb.size == 0:
            return {"matches": [], "distances": []}

        index_path = os.path.join("outputs", "faiss_index.index")
        if not os.path.exists(index_path):
            return {"matches": [], "distances": []}

        index = faiss.read_index(index_path)
        D, I = index.search(emb.astype(np.float32), k=3)
        return {"matches": I[0].tolist(), "distances": D[0].tolist()}
    except Exception as e:
        return {"error": str(e)}
    

def query_regulations(filters: dict) -> list[dict]:
    """
    Regulation search tool for agent.

    filters example:
      {
        "source_document": "MAS Notice 610",
        "risk_type": "Suspicious Transaction",
        "missing_deadline": True
      }

    Returns:
      List of dict rows matching filters.
    """
    path = os.path.join("outputs", "tagged_regulatory.csv")
    if not os.path.exists(path):
        return []

    df = pd.read_csv(path)

    # Apply filtering
    if "source_document" in filters and filters["source_document"]:
        df = df[df["source_document"].str.contains(filters["source_document"], case=False, na=False)]

    if "risk_type" in filters and filters["risk_type"]:
        df = df[df["risk_type"].str.contains(filters["risk_type"], case=False, na=False)]

    if filters.get("missing_deadline"):
        df = df[df["deadline"].isna() | (df["deadline"].astype(str).str.strip() == "")]

    if filters.get("missing_owner"):
        df = df[df["owner"].isna() | (df["owner"].astype(str).str.strip() == "")]

    return df.to_dict("records")

