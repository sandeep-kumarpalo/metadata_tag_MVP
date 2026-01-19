import streamlit as st
import pandas as pd
from dotenv import load_dotenv
import os
import json
from datetime import datetime

from utils.tagging_functions import (
    tag_pii_messages,
    tag_aml_transactions,
    tag_regulatory_obligations,
)
from utils.semantic_layer_builder import (
    build_dbt_core_layer,
    build_dbt_faiss_hybrid_layer,
)
from utils.agent_builder import (
    create_agent_without_layer,
    create_agent_with_layer,
    create_agent_with_vector_layer,
    create_agent_without_layer_with_trace,
    create_agent_with_layer_with_trace,
    create_agent_with_vector_layer_with_trace,
)

import plotly.express as px

load_dotenv()

# ---------------------------------------------------------------------
# Page config & minimal styling
# ---------------------------------------------------------------------
st.set_page_config(
    page_title=" Banking Agentic Compliance Co-Pilot",
    page_icon="🏦",
    layout="wide",
)

st.markdown(
    """
    <style>
    /* Base & Colors */
    .stApp { background-color: #f8f9fa; } /* Light grey background */
    :root {
        --p-corporate-blue: #003366;
        --p-gold-accent: #D4AF37;
        --p-soft-red: #e57373;
        --p-soft-green: #81c784;
    }

    /* Buttons */
    .stButton > button {
        background-color: var(--p-corporate-blue);
        color: white;
        border: none;
        border-radius: 6px;
        padding: 0.4rem 0.9rem;
        font-weight: 500;
    }
    .stButton > button:hover { background-color: #002244; }
    .stSpinner > div > div { border-top-color: var(--p-corporate-blue); }

    /* Dataframes & Containers */
    .stDataFrame { border: 1px solid #dee2e6; }
    .answer-card {
        padding: 16px;
        background: #ffffff;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        margin-bottom: 8px;
        font-size: 1.0rem; /* Slightly larger font */
    }

    /* Agent response colors */
    .red-highlight { border-left: 5px solid var(--p-soft-red); }
    .green-highlight { border-left: 5px solid var(--p-soft-green); }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🏦Banking Agentic Compliance Co-Pilot")
st.markdown(
    "End-to-end demo of **metadata tagging → semantic layer → agentic access** "
    "for PII, AML and regulatory intelligence."
)

# ---------------------------------------------------------------------
# Sidebar – Data uploads and exports
# ---------------------------------------------------------------------
with st.sidebar:
    # Placeholder for a logo
    # st.image("path/to/your/logo.png", width=200)
    st.header("Data Uploads")

    pii_file = st.file_uploader(
        "Customer Communications CSV (PII)", type="csv", key="pii_upload"
    )
    aml_file = st.file_uploader(
        "Transaction Narratives CSV (AML)", type="csv", key="aml_upload"
    )
    reg_file = st.file_uploader(
        "Regulatory Paragraphs CSV", type="csv", key="reg_upload"
    )

    # Helper: tagging logs
    def log_tagging(msg: str):
        logs = st.session_state.setdefault("tagging_logs", [])
        ts = datetime.now().strftime("%H:%M:%S")
        logs.append(f"{ts} | {msg}")

    def log_semantic(msg: str):
        logs = st.session_state.setdefault("semantic_logs", [])
        ts = datetime.now().strftime("%H:%M:%S")
        logs.append(f"{ts} | {msg}")

    def log_agent(msg: str):
        logs = st.session_state.setdefault("agent_logs", [])
        ts = datetime.now().strftime("%H:%M:%S")
        logs.append(f"{ts} | {msg}")

    st.header("Exports")
    if "tagged_pii" in st.session_state:
        st.download_button(
            "Download Tagged PII CSV",
            st.session_state["tagged_pii"].to_csv(index=False),
            "tagged_pii.csv",
        )
    if "tagged_aml" in st.session_state:
        st.download_button(
            "Download Tagged AML CSV",
            st.session_state["tagged_aml"].to_csv(index=False),
            "tagged_aml.csv",
        )
    if "tagged_reg" in st.session_state:
        st.download_button(
            "Download Tagged Regulatory CSV",
            st.session_state["tagged_reg"].to_csv(index=False),
            "tagged_regulatory.csv",
        )

    if st.button("Export Master JSON"):
        master = {
            "generated_at": datetime.now().isoformat(),
            "tagged_pii": st.session_state.get("tagged_pii", pd.DataFrame()).to_dict(
                "records"
            ),
            "tagged_aml": st.session_state.get("tagged_aml", pd.DataFrame()).to_dict(
                "records"
            ),
            "tagged_reg": st.session_state.get("tagged_reg", pd.DataFrame()).to_dict(
                "records"
            ),
        }
        st.download_button(
            "Download JSON",
            json.dumps(master, indent=2),
            "master_export.json",
        )

# ---------------------------------------------------------------------
# Helper: explode tag columns for charts
# ---------------------------------------------------------------------
def _explode_tag_column(df: pd.DataFrame, col: str, out_col: str) -> pd.DataFrame:
    """Turn list / JSON-string tag column into (tag, count) DataFrame."""
    if col not in df.columns:
        return pd.DataFrame()

    series = df[col].dropna()

    def to_list(x):
        if isinstance(x, list):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s.startswith("[") and s.endswith("]"):
                try:
                    return json.loads(s.replace("'", '"'))
                except Exception:
                    return [s]
            return [s]
        return [str(x)]

    tags = []
    for v in series:
        for t in to_list(v):
            t_clean = str(t).strip().strip("'").strip('"')
            if t_clean:
                tags.append(t_clean)

    if not tags:
        return pd.DataFrame()

    vc = pd.Series(tags).value_counts().reset_index()
    vc.columns = [out_col, "count"]
    return vc


def _parse_breakdown_metric(raw) -> dict:
    """
    Convert a breakdown metric that might be a dict OR a JSON string
    into a clean {label: count, ...} dictionary.
    """
    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                # Handle both proper JSON and Python-style dict strings
                return json.loads(s.replace("'", '"'))
            except Exception:
                return {}
    return {}



# ---------------------------------------------------------------------
# Main tabs
# ---------------------------------------------------------------------
tab_tagging, tab_semantic, tab_agent = st.tabs(
    ["Metadata Tagging", "Semantic Layer", "Agentic Access"]
)

# =====================================================================
# METADATA TAGGING
# =====================================================================
with tab_tagging:
    st.header("Metadata Tagging")

    tagging_tab_pii, tagging_tab_aml, tagging_tab_reg = st.tabs(
        [
            "PII & Sensitive Data",
            "AML Risk Tagging",
            "Regulatory Obligations",
        ]
    )

    # ---------------------- PII & Sensitive Data ---------------------
    with tagging_tab_pii:
        st.subheader("PII & Sensitive Data Protector")

        if pii_file is not None and (
            "tagged_pii" not in st.session_state
            or st.button("Re-process PII", key="repii")
        ):
            with st.spinner("Tagging PII: NRIC, salary, account numbers..."):
                log_tagging("Starting PII tagging (PII tab)...")
                df = pd.read_csv(pii_file)
                tagged_df = tag_pii_messages(df)
                st.session_state["tagged_pii"] = tagged_df
                os.makedirs("outputs", exist_ok=True)
                tagged_df.to_excel("outputs/tagged_pii.xlsx", index=False)
                tagged_df.to_csv("outputs/tagged_pii.csv", index=False)
                risk_counts = (
                    tagged_df["risk_flag"].value_counts().to_dict()
                    if "risk_flag" in tagged_df.columns
                    else {}
                )
                log_tagging(
                    f"PII tagging complete → {len(tagged_df)} records, risk={risk_counts}"
                )

        if "tagged_pii" in st.session_state:
            df = st.session_state["tagged_pii"].copy()

            # Styled dataframe: highlight High/Critical
            def _style_pii(row):
                color = ""
                if str(row.get("risk_flag", "")).lower() in ["high", "critical"]:
                    color = "background-color: #ffe6e6; font-weight: bold;"
                return [color] * len(row)

            styled = df.style.apply(_style_pii, axis=1)
            st.dataframe(styled, use_container_width=True, height=450)

            # Downloads
            col1, col2 = st.columns(2)
            with col1:
                if os.path.exists("outputs/tagged_pii.xlsx"):
                    st.download_button(
                        "Download PII Excel",
                        open("outputs/tagged_pii.xlsx", "rb").read(),
                        "tagged_pii.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            with col2:
                if os.path.exists("outputs/tagged_pii.csv"):
                    st.download_button(
                        "Download PII CSV",
                        open("outputs/tagged_pii.csv", "rb").read(),
                        "tagged_pii.csv",
                        "text/csv",
                    )

            # Real charts for PII
            if "risk_flag" in df.columns:
                risk_counts = (
                    df["risk_flag"].value_counts().reset_index()
                    if not df["risk_flag"].isna().all()
                    else pd.DataFrame()
                )
                if not risk_counts.empty:
                    risk_counts.columns = ["risk_flag", "count"]
                    st.markdown("**PII risk distribution**")
                    fig_risk = px.bar(
                        risk_counts,
                        x="risk_flag",
                        y="count",
                        title="Count of messages by PII risk flag",
                    )
                    st.plotly_chart(fig_risk, use_container_width=True)

            # PII entity frequency
            if "pii_entities" in df.columns:
                tag_counts = _explode_tag_column(df, "pii_entities", "pii_entity")
                if not tag_counts.empty:
                    st.markdown("**Top PII entities detected**")
                    fig_ent = px.bar(
                        tag_counts.head(15),
                        x="pii_entity",
                        y="count",
                        title="PII Entity Frequency",
                    )
                    st.plotly_chart(fig_ent, use_container_width=True)

    # ---------------------- AML Risk Tagging -------------------------
    with tagging_tab_aml:
        st.subheader("AML Risk Tagging")

        if aml_file is not None and (
            "tagged_aml" not in st.session_state
            or st.button("Re-process AML", key="reaml")
        ):
            with st.spinner("Tagging AML: structuring, crypto, layering..."):
                log_tagging("Starting AML tagging (AML tab)...")
                df = pd.read_csv(aml_file)
                tagged_df = tag_aml_transactions(df)
                st.session_state["tagged_aml"] = tagged_df
                os.makedirs("outputs", exist_ok=True)
                tagged_df.to_excel("outputs/tagged_aml.xlsx", index=False)
                tagged_df.to_csv("outputs/tagged_aml.csv", index=False)
                high_risk = 0
                if "risk_score" in tagged_df.columns:
                    high_risk = (tagged_df["risk_score"] >= 8).sum()
                log_tagging(
                    f"AML tagging complete → {len(tagged_df)} records, high-risk={high_risk}"
                )

        if "tagged_aml" in st.session_state:
            df = st.session_state["tagged_aml"].copy()
            st.dataframe(df, use_container_width=True, height=450)

            col1, col2 = st.columns(2)
            with col1:
                if os.path.exists("outputs/tagged_aml.xlsx"):
                    st.download_button(
                        "Download AML Excel",
                        open("outputs/tagged_aml.xlsx", "rb").read(),
                        "tagged_aml.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            with col2:
                if os.path.exists("outputs/tagged_aml.csv"):
                    st.download_button(
                        "Download AML CSV",
                        open("outputs/tagged_aml.csv", "rb").read(),
                        "tagged_aml.csv",
                        "text/csv",
                    )

            # AML risk distribution
            if "risk_score" in df.columns:
                st.markdown("**Distribution of AML risk scores**")
                fig_risk = px.histogram(
                    df,
                    x="risk_score",
                    nbins=10,
                    title="AML Risk Score Distribution",
                )
                st.plotly_chart(fig_risk, use_container_width=True)

            # AML tag frequencies
            if "aml_tags" in df.columns:
                tag_counts = _explode_tag_column(df, "aml_tags", "aml_tag")
                if not tag_counts.empty:
                    st.markdown("**Top AML typologies**")
                    fig_tags = px.bar(
                        tag_counts.head(15),
                        x="aml_tag",
                        y="count",
                        title="AML Tags Frequency",
                    )
                    st.plotly_chart(fig_tags, use_container_width=True)

    # ---------------------- Regulatory Obligations -------------------
    with tagging_tab_reg:
        st.subheader("Regulatory Obligations Tagging")

        if reg_file is not None and (
            "tagged_reg" not in st.session_state
            or st.button("Re-process Regulatory", key="rereg")
        ):
            with st.spinner("Tagging regulatory paragraphs..."):
                log_tagging("Starting Regulatory tagging (Reg tab)...")
                df = pd.read_csv(reg_file)
                tagged_df = tag_regulatory_obligations(df)
                st.session_state["tagged_reg"] = tagged_df
                os.makedirs("outputs", exist_ok=True)
                tagged_df.to_excel("outputs/tagged_regulatory.xlsx", index=False)
                tagged_df.to_csv("outputs/tagged_regulatory.csv", index=False)
                owners = (
                    tagged_df["owner"].value_counts().to_dict()
                    if "owner" in tagged_df.columns
                    else {}
                )
                log_tagging(
                    f"Regulatory tagging complete → {len(tagged_df)} paragraphs, owners={owners}"
                )

        if "tagged_reg" in st.session_state:
            df = st.session_state["tagged_reg"].copy()
            st.dataframe(df, use_container_width=True, height=450)

            col1, col2 = st.columns(2)
            with col1:
                if os.path.exists("outputs/tagged_regulatory.xlsx"):
                    st.download_button(
                        "Download Regulatory Excel",
                        open("outputs/tagged_regulatory.xlsx", "rb").read(),
                        "tagged_regulatory.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            with col2:
                if os.path.exists("outputs/tagged_regulatory.csv"):
                    st.download_button(
                        "Download Regulatory CSV",
                        open("outputs/tagged_regulatory.csv", "rb").read(),
                        "tagged_regulatory.csv",
                        "text/csv",
                    )

            # Owner distribution
            if "owner" in df.columns:
                owner_counts = df["owner"].value_counts().reset_index()
                owner_counts.columns = ["owner", "count"]
                st.markdown("**Obligations by owner**")
                fig_owner = px.bar(
                    owner_counts,
                    x="owner",
                    y="count",
                    title="Regulatory Obligations by Owner",
                )
                st.plotly_chart(fig_owner, use_container_width=True)

            # Source document distribution
            if "source_document" in df.columns:
                src_counts = df["source_document"].value_counts().reset_index()
                src_counts.columns = ["source_document", "count"]
                st.markdown("**Obligations by document (MAS / HKMA / Basel, etc.)**")
                fig_src = px.bar(
                    src_counts,
                    x="source_document",
                    y="count",
                    title="Regulatory Obligations by Source Document",
                )
                st.plotly_chart(fig_src, use_container_width=True)

    # Tagging logs
    with st.expander("Tagging Logs"):
        logs = st.session_state.get("tagging_logs", [])
        if logs:
            st.text("\n".join(logs))
        else:
            st.write("No tagging logs yet – run PII/AML/Reg tagging first.")

    st.markdown(
        "**Tagging pipeline:** Raw CSV → LLM-based tagging → Tagged PII/AML/Reg tables"
    )

# =====================================================================
# SEMANTIC LAYER
# =====================================================================
with tab_semantic:
    st.header("Semantic Layer")

    # Only two tabs now: dbt Core and Hybrid
    sem_tab_core, sem_tab_hybrid = st.tabs(
        ["dbt Core", "dbt + FAISS (Hybrid)"]
    )

    # ---------------------- dbt Core -------------------------------
        # ---------------------- dbt Core -------------------------------
    with sem_tab_core:
        st.subheader("dbt Core Semantic Layer")
        if st.button("Build dbt Core Layer"):
            with st.spinner("Building dbt Core semantic layer..."):
                tagged_data = {
                    "pii": st.session_state.get("tagged_pii", pd.DataFrame()),
                    "aml": st.session_state.get("tagged_aml", pd.DataFrame()),
                    "reg": st.session_state.get("tagged_reg", pd.DataFrame()),
                }
                log_semantic("Running dbt Core layer build...")
                layer = build_dbt_core_layer(tagged_data)
                st.session_state["semantic_dbt_core"] = layer
                log_semantic(f"dbt Core layer built with metrics: {layer.get('metrics', {})}")
            st.success("dbt Core layer built.")

        if "semantic_dbt_core" in st.session_state:
            layer = st.session_state["semantic_dbt_core"]
            metrics = layer.get("metrics", {})
            status = layer.get("status", "")

            st.write(f"**Status:** {status}")

            if metrics:
                # --------- Split scalar vs breakdown metrics ---------
                scalar_metrics = {
                    k: v
                    for k, v in metrics.items()
                    if not k.endswith("_breakdown")
                }

                # Show scalar metrics as a normal table
                if scalar_metrics:
                    st.dataframe(
                        pd.DataFrame([scalar_metrics]),
                        use_container_width=True,
                    )

                # KPI cards (AML + PII + total regs)
                col_a, col_b, col_c, col_d = st.columns(4)
                with col_a:
                    st.metric(
                        "AML High-Risk Cases",
                        value=metrics.get("aml_high_risk_count", 0),
                    )
                with col_b:
                    avg_risk = metrics.get("avg_risk_score", 0.0)
                    st.metric(
                        "Avg AML Risk Score",
                        value=f"{avg_risk:.2f}" if avg_risk else "0.00",
                    )
                with col_c:
                    st.metric(
                        "Critical PII Cases",
                        value=metrics.get("pii_critical_count", 0),
                    )
                with col_d:
                    st.metric(
                        "Total Reg Obligations",
                        value=metrics.get("reg_total_paragraphs", 0),
                    )

                # Simple bar for core AML/PII/reg scalar metrics
                metric_rows = []
                if "aml_high_risk_count" in metrics:
                    metric_rows.append(
                        {
                            "metric": "AML High-Risk Cases",
                            "value": metrics["aml_high_risk_count"],
                        }
                    )
                if "pii_critical_count" in metrics:
                    metric_rows.append(
                        {
                            "metric": "Critical PII Cases",
                            "value": metrics["pii_critical_count"],
                        }
                    )
                if "reg_total_paragraphs" in metrics:
                    metric_rows.append(
                        {
                            "metric": "Total Reg Obligations",
                            "value": metrics["reg_total_paragraphs"],
                        }
                    )

                if metric_rows:
                    metric_df = pd.DataFrame(metric_rows)
                    fig_bar = px.bar(
                        metric_df,
                        x="metric",
                        y="value",
                        title="Key Risk & Regulation Metrics (dbt Core Layer)",
                    )
                    st.plotly_chart(
                        fig_bar,
                        use_container_width=True,
                        key="core_metric_bar",
                    )

                # --------- Regulation breakdown charts ---------
                st.markdown("### Regulation Breakdown (dbt Core)")

                # 1) Owner breakdown
                owner_raw = metrics.get("reg_owner_breakdown")
                owner_dict = _parse_breakdown_metric(owner_raw)
                if owner_dict:
                    owner_df = (
                        pd.DataFrame(
                            list(owner_dict.items()), columns=["owner", "count"]
                        )
                        .sort_values("count", ascending=False)
                    )
                    fig_owner = px.bar(
                        owner_df,
                        x="owner",
                        y="count",
                        title="Regulatory Obligations by Owner",
                    )
                    st.plotly_chart(
                        fig_owner,
                        use_container_width=True,
                        key="core_reg_owner",
                    )

                # 2) Document breakdown
                doc_raw = metrics.get("reg_doc_breakdown")
                doc_dict = _parse_breakdown_metric(doc_raw)
                if doc_dict:
                    doc_df = (
                        pd.DataFrame(
                            list(doc_dict.items()),
                            columns=["source_document", "count"],
                        )
                        .sort_values("count", ascending=False)
                    )
                    fig_doc = px.bar(
                        doc_df,
                        x="source_document",
                        y="count",
                        title="Regulatory Obligations by Source Document",
                    )
                    st.plotly_chart(
                        fig_doc,
                        use_container_width=True,
                        key="core_reg_doc",
                    )

                # 3) Risk-type breakdown
                risk_raw = metrics.get("reg_risk_type_breakdown")
                risk_dict = _parse_breakdown_metric(risk_raw)
                if risk_dict:
                    risk_df = (
                        pd.DataFrame(
                            list(risk_dict.items()), columns=["risk_type", "count"]
                        )
                        .sort_values("count", ascending=False)
                    )
                    fig_risk = px.bar(
                        risk_df,
                        x="risk_type",
                        y="count",
                        title="Regulatory Obligations by Risk Type",
                    )
                    st.plotly_chart(
                        fig_risk,
                        use_container_width=True,
                        key="core_reg_risk",
                    )


    # ---------------------- dbt + FAISS ----------------------------
        # ---------------------- dbt + FAISS ----------------------------
    with sem_tab_hybrid:
        st.subheader("dbt + FAISS (Hybrid Semantic Layer)")
        if st.button("Build Hybrid Layer"):
            with st.spinner("Building dbt + FAISS hybrid semantic layer..."):
                tagged_data = {
                    "pii": st.session_state.get("tagged_pii", pd.DataFrame()),
                    "aml": st.session_state.get("tagged_aml", pd.DataFrame()),
                    "reg": st.session_state.get("tagged_reg", pd.DataFrame()),
                }
                log_semantic("Running dbt + FAISS hybrid layer build...")
                layer = build_dbt_faiss_hybrid_layer(tagged_data)
                st.session_state["semantic_hybrid"] = layer
                log_semantic(
                    f"Hybrid layer built with metrics: {layer.get('metrics', {})}"
                )
            st.success("Hybrid semantic layer built.")

        if "semantic_hybrid" in st.session_state:
            layer = st.session_state["semantic_hybrid"]
            metrics = layer.get("metrics", {})
            status = layer.get("status", "")

            st.write(f"**Status:** {status}")
            if metrics:
                # --------- Split scalar vs breakdown metrics ---------
                scalar_metrics = {
                    k: v
                    for k, v in metrics.items()
                    if not k.endswith("_breakdown")
                }

                if scalar_metrics:
                    st.dataframe(
                        pd.DataFrame([scalar_metrics]),
                        use_container_width=True,
                    )

                col_a, col_b, col_c, col_d, col_e = st.columns(5)
                with col_a:
                    st.metric(
                        "AML High-Risk Cases",
                        value=metrics.get("aml_high_risk_count", 0),
                    )
                with col_b:
                    avg_risk = metrics.get("avg_risk_score", 0.0)
                    st.metric(
                        "Avg AML Risk Score",
                        value=f"{avg_risk:.2f}" if avg_risk else "0.00",
                    )
                with col_c:
                    st.metric(
                        "Critical PII Cases",
                        value=metrics.get("pii_critical_count", 0),
                    )
                with col_d:
                    st.metric(
                        "Total Reg Obligations",
                        value=metrics.get("reg_total_paragraphs", 0),
                    )
                with col_e:
                    st.metric("FAISS Index Size", metrics.get("faiss_size", 0))

                metric_rows = []
                if "aml_high_risk_count" in metrics:
                    metric_rows.append(
                        {
                            "metric": "AML High-Risk Cases",
                            "value": metrics["aml_high_risk_count"],
                        }
                    )
                if "pii_critical_count" in metrics:
                    metric_rows.append(
                        {
                            "metric": "Critical PII Cases",
                            "value": metrics["pii_critical_count"],
                        }
                    )
                if "reg_total_paragraphs" in metrics:
                    metric_rows.append(
                        {
                            "metric": "Total Reg Obligations",
                            "value": metrics["reg_total_paragraphs"],
                        }
                    )
                if "faiss_size" in metrics:
                    metric_rows.append(
                        {
                            "metric": "FAISS Index Size",
                            "value": metrics["faiss_size"],
                        }
                    )

                if metric_rows:
                    metric_df = pd.DataFrame(metric_rows)
                    fig_bar = px.bar(
                        metric_df,
                        x="metric",
                        y="value",
                        title="Key Metrics (Hybrid Layer)",
                    )
                    st.plotly_chart(
                        fig_bar,
                        use_container_width=True,
                        key="hybrid_metric_bar",
                    )

                # --------- Regulation breakdown charts (same as dbt core) ---------
                st.markdown("### Regulation Breakdown (Hybrid Layer)")

                owner_raw = metrics.get("reg_owner_breakdown")
                owner_dict = _parse_breakdown_metric(owner_raw)
                if owner_dict:
                    owner_df = (
                        pd.DataFrame(
                            list(owner_dict.items()), columns=["owner", "count"]
                        )
                        .sort_values("count", ascending=False)
                    )
                    fig_owner = px.bar(
                        owner_df,
                        x="owner",
                        y="count",
                        title="Regulatory Obligations by Owner",
                    )
                    st.plotly_chart(
                        fig_owner,
                        use_container_width=True,
                        key="hybrid_reg_owner",
                    )

                doc_raw = metrics.get("reg_doc_breakdown")
                doc_dict = _parse_breakdown_metric(doc_raw)
                if doc_dict:
                    doc_df = (
                        pd.DataFrame(
                            list(doc_dict.items()),
                            columns=["source_document", "count"],
                        )
                        .sort_values("count", ascending=False)
                    )
                    fig_doc = px.bar(
                        doc_df,
                        x="source_document",
                        y="count",
                        title="Regulatory Obligations by Source Document",
                    )
                    st.plotly_chart(
                        fig_doc,
                        use_container_width=True,
                        key="hybrid_reg_doc",
                    )

                risk_raw = metrics.get("reg_risk_type_breakdown")
                risk_dict = _parse_breakdown_metric(risk_raw)
                if risk_dict:
                    risk_df = (
                        pd.DataFrame(
                            list(risk_dict.items()), columns=["risk_type", "count"]
                        )
                        .sort_values("count", ascending=False)
                    )
                    fig_risk = px.bar(
                        risk_df,
                        x="risk_type",
                        y="count",
                        title="Regulatory Obligations by Risk Type",
                    )
                    st.plotly_chart(
                        fig_risk,
                        use_container_width=True,
                        key="hybrid_reg_risk",
                    )


    with st.expander("Semantic Layer Build Logs"):
        logs = st.session_state.get("semantic_logs", [])
        if logs:
            st.text("\n".join(logs))
        else:
            st.write(
                "No semantic-layer logs yet – build dbt Core / Hybrid layer first."
            )

    st.markdown(
        "**Semantic layer pipeline:** Tagged tables → dbt models → metrics & vectors → agentic access"
    )

# =====================================================================
# AGENTIC ACCESS
# =====================================================================
with tab_agent:
    st.header("Agentic Access")

    query = st.text_input(
        "Ask a question (e.g. NRIC leaks, salary details, high-risk crypto, MAS 610, SAR for T028)"
    )

    st.markdown("---")

    col1, col2 = st.columns(2)

    # ---------------------- Without Layer ---------------------------
    with col1:
        st.markdown("#### Without Layer (Baseline Engine)")
        st.markdown(
            "<p style='font-size:0.9rem; color:#6c757d;'>Runs on raw, untagged data. Answers may be incomplete or lack risk context.</p>",
            unsafe_allow_html=True)
        if st.button("Run Without Layer"):
            if not query.strip():
                st.warning("Please enter a query first.")
            else:
                with st.spinner("Running baseline agent (without semantic layer)..."):
                    agent_without = create_agent_without_layer_with_trace()
                    trace_without = agent_without(query)
                    st.session_state["trace_without"] = trace_without
                    st.session_state["response_without"] = trace_without["answer"]

                    # Agent log
                    no_hit = (
                        trace_without.get("hit_count", 0) == 0
                        or "No results found" in (trace_without.get("answer") or "")
                    )
                    log_agent(
                        f"mode={trace_without.get('mode')} | "
                        f"intent={trace_without.get('intent')} | "
                        f"tool={trace_without.get('tool_name')} | "
                        f"hits={trace_without.get('hit_count')} | "
                        f"no_results={no_hit} | "
                        f"query='{query}'"
                    )

        if "trace_without" in st.session_state:
            tw = st.session_state["trace_without"]
            answer_raw = tw.get("answer", "") or ""
            # Convert newlines to <br> so bullets render nicely inside the HTML card
            answer_html = answer_raw.replace("\n", "<br>")

            with st.container():
                st.markdown(
                    f"<div class='answer-card red-highlight'>{answer_html}</div>",
                    unsafe_allow_html=True,
                )

            with st.expander("Tool Trace (Without Layer)"):
                st.write(f"**Intent:** {tw.get('intent')}")
                st.write(f"**Mode:** {tw.get('mode')}")
                st.write(f"**Tool:** {tw.get('tool_name')}")
                st.write(f"**Hit count:** {tw.get('hit_count', 0)}")

                preview = tw.get("preview") or []
                if preview:
                    st.write("**Preview of tool hits (first few rows):**")
                    try:
                        st.dataframe(pd.DataFrame(preview), use_container_width=True)
                    except Exception:
                        st.json(preview)
                else:
                    st.write("No hits to preview (tool returned empty result set).")

    # ---------------------- With Layer / Hybrid ---------------------
    with col2:
        st.markdown("#### With Layer (Semantic Engine)")
        st.markdown(
            "<p style='font-size:0.9rem; color:#6c757d;'>Runs on tagged, enriched data. Provides grounded, auditable, and context-aware answers.</p>",
            unsafe_allow_html=True)

        if st.button("Run With Layer / Hybrid"):
            if not query.strip():
                st.warning("Please enter a query first.")
            else:
                with st.spinner("Running semantic-layer agent..."):

                    # --- Always use the best agent (Hybrid) and auto-build FAISS if needed ---
                    index_path = "outputs/faiss_index.index"
                    if not os.path.exists(index_path):
                        tagged_data = {
                            "pii": st.session_state.get(
                                "tagged_pii", pd.DataFrame()
                            ),
                            "aml": st.session_state.get(
                                "tagged_aml", pd.DataFrame()
                            ),
                            "reg": st.session_state.get(
                                "tagged_reg", pd.DataFrame()
                            ),
                        }
                        log_semantic(
                            "Auto-building Hybrid Layer before agent run..."
                        )
                        _ = build_dbt_faiss_hybrid_layer(tagged_data)

                    agent_with = create_agent_with_vector_layer_with_trace()

                    trace_with = agent_with(query)
                    st.session_state["trace_with"] = trace_with
                    st.session_state["response_with"] = trace_with["answer"]

                    no_hit = (
                        trace_with.get("hit_count", 0) == 0
                        or "No results found" in (trace_with.get("answer") or "")
                    )
                    log_agent(
                        f"mode={trace_with.get('mode')} | "
                        f"intent={trace_with.get('intent')} | "
                        f"tool={trace_with.get('tool_name')} | "
                        f"hits={trace_with.get('hit_count')} | "
                        f"no_results={no_hit} | "
                        f"query='{query}'"
                    )

        if "trace_with" in st.session_state:
            tw = st.session_state["trace_with"]
            answer_raw = tw.get("answer", "") or ""
            answer_html = answer_raw.replace("\n", "<br>")

            with st.container():
                st.markdown(
                    f"<div class='answer-card green-highlight'>{answer_html}</div>",
                    unsafe_allow_html=True,
                )

            with st.expander("Tool Trace (With Layer / Hybrid)"):

                st.write(f"**Intent:** {tw.get('intent')}")
                st.write(f"**Mode:** {tw.get('mode')}")
                st.write(f"**Tool:** {tw.get('tool_name')}")
                st.write(f"**Hit count:** {tw.get('hit_count', 0)}")

                preview = tw.get("preview") or []
                if preview:
                    st.write("**Preview of tool hits (first few rows):**")
                    try:
                        st.dataframe(pd.DataFrame(preview), use_container_width=True)
                    except Exception:
                        st.json(preview)
                else:
                    st.write("No hits to preview (tool returned empty result set).")

    # ---------------------- Comparison Summary ----------------------
    if "trace_without" in st.session_state and "trace_with" in st.session_state:
        tw_out = st.session_state["trace_without"]
        tw_with = st.session_state["trace_with"]

        st.markdown("---")
        st.subheader("Agent Comparison Summary")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Baseline signal (raw data)**")
            resp = tw_out.get("answer", "")
            st.metric("Answer Length", f"{len(resp)} chars")
            st.metric("Hit Count", tw_out.get("hit_count", 0))
            st.write(f"**Intent:** {tw_out.get('intent')} | **Tool:** {tw_out.get('tool_name')}")
            if tw_out.get("hit_count", 0) == 0 or "No results found" in resp:
                st.write("Result: No hits – raw data struggled.")
            else:
                st.write("Result: Answer produced directly from raw tools.")

        with col_b:
            st.markdown("**Semantic-layer signal (tagged data)**")
            resp = tw_with.get("answer", "")
            st.metric("Answer Length", f"{len(resp)} chars")
            st.metric("Hit Count", tw_with.get("hit_count", 0))
            st.write(f"**Intent:** {tw_with.get('intent')} | **Tool:** {tw_with.get('tool_name')}")
            if tw_with.get("hit_count", 0) == 0 or "No results found" in resp:
                st.write("Result: No hits – even semantic layer found nothing.")
            else:
                st.write("Result: Grounded answer using tagged metadata / vectors.")

    with st.expander("Agent Logs / Execution Trail"):
        logs = st.session_state.get("agent_logs", [])
        if logs:
            st.text("\n".join(logs))
        else:
            st.write("No agent runs yet – ask a question and run one of the agents.")

    st.markdown(
        "**Agent flow:** User query → Router & tools → (optional) semantic layer → answer"
    )
