import os
import pandas as pd
import numpy as np

from utils.semantic_layer_builder import (
    embed_text_azure_single,
    build_dbt_faiss_hybrid_layer,
    query_vector_layer,
    safe_load,
)

print("\n===================================================")
print(" TEST 1 — Validate Azure Embedding Works")
print("===================================================")

sample_texts = [
    "Customer withdrew 50k in cash",
    "Structuring pattern observed across 12 accounts",
    "Incoming transfer from a high-risk jurisdiction",
]

vectors = []
for i, text in enumerate(sample_texts):
    v = embed_text_azure_single(text, idx=i)
    if v is not None:
        vectors.append(v)

if vectors:
    arr = np.vstack(vectors)
    print("Embedding shape:", arr.shape)
    print("Example dims:", arr[0][:5])
    print("✅ Azure embedding OK")
else:
    print("❌ Azure embedding FAILED — Cannot continue FAISS indexing.")


print("\n===================================================")
print(" TEST 2 — Validate Tagged CSVs Exist")
print("===================================================")

paths = {
    "AML": "outputs/tagged_aml.csv",
    "PII": "outputs/tagged_pii.csv",
    "REG": "outputs/tagged_regulatory.csv",
}

for name, path in paths.items():
    exists = os.path.exists(path)
    print(f"{name} file exists:", exists)
    if exists:
        df = safe_load(path)
        print(f"{name} shape:", df.shape)
        print(df.head(2))


print("\n===================================================")
print(" TEST 3 — Validate AML Narratives Cleanliness")
print("===================================================")

aml_path = "outputs/tagged_aml.csv"
if not os.path.exists(aml_path):
    print("❌ AML file missing — cannot test narratives.")
else:
    df_aml = safe_load(aml_path)

    has_masked = "masked_narrative" in df_aml.columns
    has_original = "original_narrative" in df_aml.columns

    print("Has masked_narrative:", has_masked)
    print("Has original_narrative:", has_original)

    if has_masked:
        nulls = df_aml["masked_narrative"].isna().sum()
        print("Null masked_narratives:", nulls)

        bad_rows = df_aml[df_aml["masked_narrative"].astype(str).str.len() <= 1]
        print("Suspicious narrative rows:", len(bad_rows))
        if len(bad_rows) > 0:
            print("Examples:\n", bad_rows.head())


print("\n===================================================")
print(" TEST 4 — Embedding ALL AML Narratives (debug)")
print("===================================================")

if not os.path.exists(aml_path):
    print("❌ AML missing — cannot embed.")
else:
    df_aml = safe_load(aml_path)
    texts = df_aml["masked_narrative"].fillna("").tolist()

    failed = []
    embedded_vectors = []

    for idx, t in enumerate(texts):
        v = embed_text_azure_single(t, idx=idx)
        if v is None:
            failed.append((idx, t[:80]))
        else:
            embedded_vectors.append(v)

    print(f"Total AML narratives: {len(texts)}")
    print(f"Successful embeddings: {len(embedded_vectors)}")
    print(f"Failed embeddings: {len(failed)}")

    if failed:
        print("❌ Some embeddings FAILED at indices:", failed[:5])
    else:
        print("✅ ALL AML narratives embedded successfully!")


print("\n===================================================")
print(" TEST 5 — Build FAISS Hybrid Layer")
print("===================================================")

tagged_data = {
    "pii": safe_load("outputs/tagged_pii.csv"),
    "aml": safe_load("outputs/tagged_aml.csv"),
    "reg": safe_load("outputs/tagged_regulatory.csv"),
}

layer = build_dbt_faiss_hybrid_layer(tagged_data)
print("Hybrid Layer Output:", layer)

faiss_index_path = "outputs/faiss_index.index"
exists = os.path.exists(faiss_index_path)
print("FAISS index file exists:", exists)

if exists:
    print("✅ FAISS index successfully saved.")
else:
    print("❌ FAISS index NOT created — Streamlit Hybrid Agent will FAIL!")


print("\n===================================================")
print(" TEST 6 — Validate Vector Search Works")
print("===================================================")

if exists:
    q = "structuring pattern"
    res = query_vector_layer(q)
    print("Query:", q)
    print("Vector search result:", res)

    if res.get("matches"):
        print("✅ Vector search OK")
    else:
        print("⚠️ Vector search returned no matches")
else:
    print("❌ Skip vector search — FAISS file missing.")


print("\n===================================================")
print(" TEST 7 — STREAMLIT READINESS CHECK")
print("===================================================")

ready = True

if not vectors:
    ready = False
    print("❌ Embedding not working.")

if not exists:
    ready = False
    print("❌ FAISS index missing — Hybrid agent will not work.")

if not os.path.exists("outputs/tagged_aml.csv"):
    ready = False
    print("❌ AML file missing.")

if not os.path.exists("outputs/tagged_pii.csv"):
    ready = False
    print("❌ PII file missing.")

if ready:
    print("✅ ALL GOOD — Streamlit can run smoothly.")
else:
    print("❌ FIX ERRORS ABOVE BEFORE RUNNING STREAMLIT.")
