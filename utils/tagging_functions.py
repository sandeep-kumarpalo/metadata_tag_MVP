import pandas as pd
from openai import AzureOpenAI
from dotenv import load_dotenv
import os
import json
import time

load_dotenv()

# Azure OpenAI client
client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", os.getenv("OPENAI_API_VERSION")),
)

deployment = os.getenv("DEPLOYMENT")

# =============== FUNCTION SCHEMAS ===============

pii_schema = {
    "name": "tag_pii_and_mask",
    "description": "Detect and mask PII in customer messages",
    "parameters": {
        "type": "object",
        "properties": {
            "message_id": {"type": "string"},
            "masked_text": {"type": "string"},
            "pii_entities": {"type": "array", "items": {"type": "string"}},
            "risk_flag": {
                "type": "string",
                "enum": ["Low", "Medium", "High", "Critical"],
            },
        },
        "required": ["masked_text", "pii_entities", "risk_flag"],
    },
}

aml_schema = {
    "name": "tag_aml_risk",
    "description": "Detect AML typologies and score risk",
    "parameters": {
        "type": "object",
        "properties": {
            "transaction_id": {"type": "string"},
            "masked_narrative": {"type": "string"},
            "pii_found": {"type": "array", "items": {"type": "string"}},
            "aml_tags": {"type": "array", "items": {"type": "string"}},
            "risk_score": {"type": "number", "minimum": 0, "maximum": 10},
            "explanation": {"type": "string"},
        },
        "required": ["masked_narrative", "aml_tags", "risk_score", "explanation"],
    },
}

reg_schema = {
    "name": "tag_regulatory_obligation",
    "description": "Extract regulatory obligation metadata",
    "parameters": {
        "type": "object",
        "properties": {
            "paragraph_id": {"type": "string"},
            "source_document": {"type": "string"},
            "regulation": {"type": "string"},
            "article": {"type": "string"},
            "risk_type": {"type": "string"},
            "business_unit": {"type": "array", "items": {"type": "string"}},
            "owner": {"type": "string"},
            "deadline": {
                "type": "string",
                "description": "e.g. Ongoing, Annual, 2026-12-31",
            },
        },
        "required": ["regulation", "risk_type", "business_unit"],
    },
}

sar_schema = {
    "name": "draft_sar",
    "description": "Draft Suspicious Activity Report in bank format",
    "parameters": {
        "type": "object",
        "properties": {
            "transaction_id": {"type": "string"},
            "customer_name": {"type": "string"},
            "narrative": {"type": "string"},
            "typology": {"type": "string"},
            "risk_score": {"type": "number"},
            "justification": {"type": "string"},
            "recommended_action": {"type": "string"},
        },
        "required": ["transaction_id", "typology", "justification", "recommended_action"],
    },
}

# =============== TAGGING CORE CALL ===============

def call_azure_function(text: str, schema: dict, system_prompt: str):
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                tools=[{"type": "function", "function": schema}],
                tool_choice={"type": "function", "function": {"name": schema["name"]}},
                temperature=0.0,
                max_tokens=500,
            )
            result = response.choices[0].message.tool_calls[0].function.arguments
            return json.loads(result)
        except Exception as e:
            time.sleep(1)
            if attempt == 2:
                return {"error": str(e)}
    return {"error": "max retries"}


# =============== TAGGING FUNCTIONS ===============

def tag_pii_messages(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tag PII in customer messages using a stricter, rule-based risk policy.

    We explicitly instruct the model to:
      - Detect NRIC, passport, account numbers, phone, salary/income, address,
        employee IDs/payroll numbers, PayNow IDs, etc.
      - Mask all PII in masked_text using placeholders like <NRIC>, <ACCOUNT>, etc.
      - Assign risk_flag according to the rules below:

        CRITICAL:
          - Any combination of NRIC with ACCOUNT NUMBER, SALARY or ADDRESS.
          - Any message containing 3 or more distinct PII entity types.
        HIGH:
          - Single NRIC, passport, or account number.
          - Passport + another PII (phone, salary, address).
        MEDIUM:
          - Salary/income, phone number, email, employee/payroll IDs on their own.
        LOW:
          - Very generic references with no concrete identifiers.

    This is Option B: we strongly bias the LLM so that "Critical" actually appears
    where we expect it, which then flows into the semantic layer metrics
    (pii_critical_count).
    """
    system = """
You are a banking PII detection expert for a Singapore retail/commercial bank.

Your tasks for EACH message:
1. Detect and list all PII entities in the text, including:
   - NRIC
   - Passport number
   - Account number / bank account
   - Phone / phone number / mobile number
   - Salary / income / compensation / payroll amount
   - Address (home, mailing)
   - Employee ID / staff ID / payroll number
   - PayNow ID or similar payment identifiers
2. Produce a masked_text where each PII value is replaced with a placeholder token
   such as <NRIC>, <PASSPORT>, <ACCOUNT>, <PHONE>, <SALARY>, <ADDRESS>, <EMP_ID>.
   Keep the rest of the sentence intact and readable.
3. Assign a risk_flag using ONLY these values:
   - Low
   - Medium
   - High
   - Critical

Apply the following STRICT risk rules:

- CRITICAL:
  - Any combination of NRIC with ACCOUNT NUMBER, SALARY or ADDRESS in the same message.
  - Any message that contains THREE OR MORE distinct PII entity types
    (e.g. NRIC + Phone + Salary, or Passport + Account + Address).
- HIGH:
  - A single NRIC, passport or account number on its own.
  - Passport combined with any other PII (phone, salary, address).
  - Any message where you strongly suspect identity theft or account takeover risk.
- MEDIUM:
  - Salary / income amount on its own.
  - Phone number, email, employee ID / payroll ID on their own.
  - PayNow IDs that are not combined with NRIC or account numbers.
- LOW:
  - Very generic references like "my salary" or "my account" with NO specific numbers.
  - Any case where you are unsure and no clear PII pattern is present.

You MUST obey these rules exactly.
Return:
  - masked_text: the fully masked message,
  - pii_entities: array of string labels for entities you detected
    (e.g. ["NRIC", "Account Number", "Salary"]),
  - risk_flag: one of ["Low", "Medium", "High", "Critical"] exactly.
"""

    results = []
    for _, row in df.iterrows():
        text = f"Message ID: {row['message_id']}\nText: {row['text']}"
        result = call_azure_function(text, pii_schema, system)
        result["original_text"] = row["text"]
        # Keep message_id for convenience if model didn't echo it
        if "message_id" not in result:
            result["message_id"] = str(row.get("message_id", ""))
        results.append(result)
    return pd.DataFrame(results)


def tag_aml_transactions(df: pd.DataFrame) -> pd.DataFrame:
    system = (
        "You are an AML expert for Singapore/HK. Detect structuring, smurfing, funnel, "
        "crypto, gambling, trade-based laundering. Score risk_score between 0 and 10. "
        "Explain briefly why in 'explanation'."
    )
    results = []
    for _, row in df.iterrows():
        text = (
            f"Transaction ID: {row['transaction_id']}\n"
            f"Amount: {row['amount_sgd']} SGD\n"
            f"Narrative: {row['narrative']}"
        )
        result = call_azure_function(text, aml_schema, system)
        result["original_narrative"] = row["narrative"]
        result["amount_sgd"] = row["amount_sgd"]
        result["date"] = row["date"]
        if "transaction_id" not in result:
            result["transaction_id"] = str(row.get("transaction_id", ""))
        results.append(result)
    return pd.DataFrame(results)


def tag_regulatory_obligations(df: pd.DataFrame) -> pd.DataFrame:
    system = (
        "You are a regulatory mapping expert for MAS, HKMA, Basel III. "
        "For each paragraph, extract regulation name/identifier, risk_type, "
        "business_unit (owner teams), and owner. Use realistic owners such as "
        "Compliance, MLRO, Operations, etc."
    )
    results = []
    for _, row in df.iterrows():
        text = (
            f"Paragraph ID: {row['paragraph_id']}\n"
            f"Source: {row['source_document']}\n"
            f"Text: {row['paragraph_text']}"
        )
        result = call_azure_function(text, reg_schema, system)
        result["original_text"] = row["paragraph_text"]
        if "paragraph_id" not in result:
            result["paragraph_id"] = str(row.get("paragraph_id", ""))
        if "source_document" not in result:
            result["source_document"] = str(row.get("source_document", ""))
        results.append(result)
    return pd.DataFrame(results)


# Export SAR schema for agent
def get_sar_schema():
    return sar_schema
