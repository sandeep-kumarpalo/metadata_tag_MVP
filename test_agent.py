import pandas as pd
import os
import json
from dotenv import load_dotenv
from utils.tagging_functions import tag_pii_messages, tag_aml_transactions, tag_regulatory_obligations
from utils.semantic_layer_builder import build_dbt_core_layer, build_dbt_faiss_hybrid_layer, build_atscale_layer
from utils.agent_builder import create_agent_without_layer, create_agent_with_layer, create_agent_with_vector_layer

# Load env
load_dotenv()

# Paths to data
DATA_DIR = "data"
PII_CSV = os.path.join(DATA_DIR, "customer_communication_logs_100_rows.csv")
AML_CSV = os.path.join(DATA_DIR, "transaction_narratives_120_rows.csv")
REG_CSV = os.path.join(DATA_DIR, "regulatory_paragraphs_45_rows.csv")

# Step 1: Run Obj1 Tagging (simple, save to outputs for agents)
print("Running Objective 1: Tagging...")
try:
    df_pii = pd.read_csv(PII_CSV)
    tagged_pii = tag_pii_messages(df_pii)
    tagged_pii.to_csv("outputs/tagged_pii.csv", index=False)
    
    df_aml = pd.read_csv(AML_CSV)
    tagged_aml = tag_aml_transactions(df_aml)
    tagged_aml.to_csv("outputs/tagged_aml.csv", index=False)
    
    df_reg = pd.read_csv(REG_CSV)
    tagged_reg = tag_regulatory_obligations(df_reg)
    tagged_reg.to_csv("outputs/tagged_regulatory.csv", index=False)
    print("Tagging complete.")
except Exception as e:
    print(f"Error in tagging: {e}")
    exit(1)
 
# Step 2: Run Obj2 Semantic Layer Builds (test all methods, print results)
print("\nRunning Objective 2: Semantic Layer Builds...")
tagged_data = {
    'pii': pd.read_csv("outputs/tagged_pii.csv"),
    'aml': pd.read_csv("outputs/tagged_aml.csv"),
    'reg': pd.read_csv("outputs/tagged_regulatory.csv")
}
try:
    # Method 1: dbt Core
    layer1 = build_dbt_core_layer(tagged_data)
    print(f"dbt Core Layer: {layer1}")

    # Method 2: dbt + FAISS
    layer2 = build_dbt_faiss_hybrid_layer(tagged_data)
    print(f"dbt + FAISS Layer: {layer2}")

    # Method 3: AtScale mock
    layer3 = build_atscale_layer(tagged_data)
    print(f"AtScale Layer: {layer3}")

    # Pass/Fail check (simple: status 'complete')
    pass_obj2 = all('status' in layer and 'complete' in layer['status'] for layer in [layer1, layer2, layer3])
    print(f"Obj2 Pass: {pass_obj2}")
except Exception as e:
    print(f"Error in layer builds: {e}")

# Step 3: Create Obj3 Agents
print("\nCreating Agents...")
agent_without = create_agent_without_layer()
agent_with = create_agent_with_layer()
agent_vector = create_agent_with_vector_layer()

# Queries and expected patterns (simple string checks for pass/fail)
queries = [
    {
        "query": "Show me any messages with NRIC leaks.",
        "expected_without": "PII Matches Found",  # Mild hallucination, but contains header
        "expected_with": "**PII Matches Found:**"  # Exact, grounded
    },
    {
        "query": "Have there been any salary details exposed in chats?",
        "expected_without": "PII Matches Found",
        "expected_with": "**PII Matches Found:**"
    },
    {
        "query": "Find high-risk transactions related to crypto.",
        "expected_without": "High-Risk Transactions",
        "expected_with": "**High-Risk Transactions:**"
    },
    {
        "query": "Show me examples of structuring.",
        "expected_without": "High-Risk Transactions",
        "expected_with": "**High-Risk Transactions:**"
    },
    {
        "query": "What are the MAS 610 rules on suspicious transactions?",
        "expected_without": "Regulatory Obligations",
        "expected_with": "**Regulatory Obligations:**"
    },
    {
        "query": "Draft a SAR for transaction T028",
        "expected_without": "SAR Drafted",
        "expected_with": "**SAR Drafted for T028**"
    }
]

# Step 4: Test Queries Automatically (pass/fail)
print("\nTesting Queries...")
for q in queries:
    print(f"\nQuery: {q['query']}")
    
    # Without layer
    resp_without = agent_without(q['query'])
    pass_without = q['expected_without'] in resp_without
    print(f"Without Layer: {resp_without}")
    print(f"Pass: {pass_without}")
    
    # With layer
    resp_with = agent_with(q['query'])
    pass_with = q['expected_with'] in resp_with
    print(f"With Layer: {resp_with}")
    print(f"Pass: {pass_with}")
    
    # With vector (similar to with layer)
    resp_vector = agent_vector(q['query'])
    pass_vector = q['expected_with'] in resp_vector
    print(f"With Vector Layer: {resp_vector}")
    print(f"Pass: {pass_vector}")