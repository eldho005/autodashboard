"""
Document Verification Engine
Extracts fields from Auto Quote, DASH, MVR, and Application Form PDFs using Vertex AI Gemini,
then cross-compares all sources to flag mismatches.
"""
import os
import re
import json
import base64
from datetime import datetime

try:
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part
    VERTEX_AI_AVAILABLE = True
except ImportError:
    VERTEX_AI_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION PROMPTS  (one per document type)
# ─────────────────────────────────────────────────────────────────────────────

QUOTE_EXTRACTION_PROMPT = """
You are extracting auto insurance quote data. Return ONLY valid JSON, no markdown, no extra text.

Extract into this exact schema (use null for missing fields):
{
  "personal": {
    "first_name": "",
    "middle_name": "",
    "last_name": "",
    "dob": "",
    "gender": "",
    "marital_status": "",
    "address": "",
    "city": "",
    "province": "",
    "postal_code": "",
    "phone": "",
    "email": ""
  },
  "licence": {
    "licence_number": "",
    "licence_class": "",
    "licence_province": "",
    "g_date": "",
    "g2_date": "",
    "g1_date": "",
    "date_first_insured": ""
  },
  "insurance_history": {
    "current_carrier": "",
    "date_with_current_carrier": "",
    "date_first_insured": ""
  },
  "vehicles": [
    {
      "year": "",
      "make": "",
      "model": "",
      "vin": "",
      "annual_km": "",
      "use": "",
      "purchase_date": "",
      "winter_tires": "",
      "leased": ""
    }
  ],
  "claims": [
    {
      "date": "",
      "type": "",
      "at_fault": "",
      "amount": ""
    }
  ],
  "convictions": [
    {
      "date": "",
      "description": "",
      "conviction_date": ""
    }
  ]
}

Notes:
- Dates: use format MM/DD/YYYY as found in the document (do not convert)
- Licence number: include dashes exactly as shown
- For drivers section: extract driver 1 (principal insured) fields only
- Return null for any truly absent field, empty string "" is also acceptable
"""

DASH_EXTRACTION_PROMPT = """
You are extracting data from a DASH (Driver Abstract / Driver History Summary) report for Ontario, Canada.
Return ONLY valid JSON, no markdown, no extra text.

Extract into this exact schema (use null for missing fields):
{
  "personal": {
    "first_name": "",
    "middle_name": "",
    "last_name": "",
    "dob": "",
    "gender": "",
    "marital_status": "",
    "address": ""
  },
  "licence": {
    "licence_number": "",
    "licence_class": "",
    "licence_province": "ON",
    "years_licensed": "",
    "date_first_licensed": ""
  },
  "insurance_history": {
    "current_carrier": "",
    "date_first_insured": "",
    "previous_carriers": []
  },
  "vehicles": [
    {
      "year": "",
      "make": "",
      "model": "",
      "vin": "",
      "annual_km": "",
      "use": ""
    }
  ],
  "claims": [
    {
      "date": "",
      "type": "",
      "at_fault": "",
      "amount": ""
    }
  ],
  "convictions": [
    {
      "date": "",
      "description": "",
      "conviction_date": ""
    }
  ],
  "lapses": [
    {
      "start_date": "",
      "end_date": "",
      "reason": ""
    }
  ]
}

Notes:
- Extract ALL convictions and claims listed in the document
- For lapses/gaps in insurance coverage, populate the lapses array
- Dates: use format as found in the document (DD/MM/YYYY or MM/DD/YYYY — copy exactly)
"""

MVR_EXTRACTION_PROMPT = """
You are extracting data from an Ontario MVR (Motor Vehicle Record / Driver Abstract) issued by the Ministry of Transportation.
Return ONLY valid JSON, no markdown, no extra text.

Extract into this exact schema (use null for missing fields):
{
  "personal": {
    "first_name": "",
    "middle_name": "",
    "last_name": "",
    "dob": "",
    "gender": "",
    "address": ""
  },
  "licence": {
    "licence_number": "",
    "licence_class": "",
    "licence_province": "ON",
    "expiry_date": "",
    "demerit_points": "",
    "g_date": "",
    "g2_date": "",
    "g1_date": ""
  },
  "convictions": [
    {
      "offence_date": "",
      "description": "",
      "conviction_date": "",
      "demerit_points": ""
    }
  ],
  "suspensions": [
    {
      "start_date": "",
      "end_date": "",
      "type": "",
      "case_number": ""
    }
  ]
}

Notes:
- Extract ALL convictions and suspensions listed
- Demerit points at top of record: total current points
- Dates: copy exactly as shown (do not convert format)
- Suspension end_date may say "Reinstated" with a date — capture that date
"""

APPLICATION_EXTRACTION_PROMPT = """
You are extracting data from an Ontario auto insurance application form (also called a pink slip application or broker application form).
Return ONLY valid JSON, no markdown, no extra text.

Extract into this exact schema (use null for missing fields):
{
  "personal": {
    "first_name": "",
    "middle_name": "",
    "last_name": "",
    "dob": "",
    "gender": "",
    "marital_status": "",
    "address": "",
    "city": "",
    "province": "",
    "postal_code": "",
    "phone": "",
    "email": ""
  },
  "licence": {
    "licence_number": "",
    "licence_class": "",
    "g_date": "",
    "g2_date": "",
    "g1_date": "",
    "years_licensed": ""
  },
  "insurance_history": {
    "current_carrier": "",
    "date_first_insured": "",
    "date_with_current_carrier": ""
  },
  "vehicles": [
    {
      "year": "",
      "make": "",
      "model": "",
      "vin": "",
      "annual_km": "",
      "use": "",
      "purchase_date": "",
      "winter_tires": "",
      "leased": ""
    }
  ],
  "claims": [
    {
      "date": "",
      "type": "",
      "at_fault": "",
      "amount": ""
    }
  ],
  "convictions": [
    {
      "date": "",
      "description": ""
    }
  ]
}
"""

PROMPTS = {
    "quote": QUOTE_EXTRACTION_PROMPT,
    "dash": DASH_EXTRACTION_PROMPT,
    "mvr": MVR_EXTRACTION_PROMPT,
    "application": APPLICATION_EXTRACTION_PROMPT,
}


# ─────────────────────────────────────────────────────────────────────────────
# VERTEX AI EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _init_model():
    project_id = os.getenv('GOOGLE_CLOUD_PROJECT')
    location = os.getenv('GOOGLE_CLOUD_LOCATION', 'us-central1')
    if not project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT environment variable not set")
    vertexai.init(project=project_id, location=location)
    return GenerativeModel("gemini-2.0-flash")


def extract_from_pdf(pdf_bytes: bytes, doc_type: str) -> dict:
    """
    Extract structured fields from a PDF using Gemini.

    doc_type: one of 'quote', 'dash', 'mvr', 'application'
    Returns: {'success': True/False, 'data': {...}, 'error': '...'}
    """
    if not VERTEX_AI_AVAILABLE:
        return {"success": False, "error": "Vertex AI not available"}

    prompt = PROMPTS.get(doc_type)
    if not prompt:
        return {"success": False, "error": f"Unknown doc_type: {doc_type}"}

    try:
        model = _init_model()
        pdf_part = Part.from_data(data=pdf_bytes, mime_type="application/pdf")
        response = model.generate_content([pdf_part, prompt])
        text = response.text.strip()

        # Strip markdown code fences
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        data = json.loads(text)
        print(f"[DOC_VERIFIER] Extracted {doc_type}: {len(str(data))} chars")
        return {"success": True, "data": data}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON parse error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# NORMALIZATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _norm_str(v) -> str:
    """Lowercase, strip, collapse spaces."""
    if v is None:
        return ""
    return re.sub(r'\s+', ' ', str(v).lower().strip())


def _norm_licence(v) -> str:
    """Remove all dashes/spaces from licence number."""
    if not v:
        return ""
    return re.sub(r'[\s\-]', '', str(v).upper())


def _norm_date(v) -> str:
    """
    Try to parse and normalise date to YYYY-MM-DD for comparison.
    Falls back to cleaned original string if unparseable.
    """
    if not v:
        return ""
    s = str(v).strip()
    formats = [
        "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d",
        "%m-%d-%Y", "%d-%m-%Y", "%b %d, %Y", "%B %d, %Y",
        "%m/%d/%y", "%d/%m/%y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Clean up and return
    return re.sub(r'\s+', '-', s.lower())


def _norm_phone(v) -> str:
    if not v:
        return ""
    return re.sub(r'\D', '', str(v))


def _norm_km(v) -> str:
    """Normalise km value: strip commas, spaces, units."""
    if not v:
        return ""
    return re.sub(r'[^\d]', '', str(v))


def _norm_bool(v) -> str:
    if not v:
        return ""
    s = str(v).lower().strip()
    if s in ('yes', 'true', '1', 'y'):
        return 'yes'
    if s in ('no', 'false', '0', 'n'):
        return 'no'
    return s


def _safe_get(data: dict, *keys, default=""):
    """Safe nested get."""
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur if cur is not None else default


# ─────────────────────────────────────────────────────────────────────────────
# COMPARISON LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def _cmp(label, quote_raw, dash_raw, mvr_raw, app_raw,
          norm_fn=_norm_str, sources=("quote", "dash", "mvr", "app")):
    """
    Build a single comparison row.
    sources controls which sources are expected to have this field (for MISSING logic).
    """
    vals = {
        "quote": norm_fn(quote_raw),
        "dash":  norm_fn(dash_raw),
        "mvr":   norm_fn(mvr_raw),
        "app":   norm_fn(app_raw),
    }
    raws = {
        "quote": quote_raw or "",
        "dash":  dash_raw or "",
        "mvr":   mvr_raw or "",
        "app":   app_raw or "",
    }

    # Collect non-empty values for sources that are expected
    populated = {s: vals[s] for s in sources if vals[s]}

    if len(populated) == 0:
        status = "N/A"
    elif len(populated) == 1:
        status = "MISSING"   # Only one source has a value
    else:
        unique = set(populated.values())
        status = "MATCH" if len(unique) == 1 else "MISMATCH"

    return {
        "field": label,
        "quote": raws["quote"],
        "dash":  raws["dash"],
        "mvr":   raws["mvr"],
        "app":   raws["app"],
        "status": status,
    }


def compare_extractions(quote: dict, dash: dict, mvr: dict, app: dict) -> dict:
    """
    Cross-compare extracted data from all 4 documents.
    Returns structured comparison result with sections.
    """
    q = quote or {}
    d = dash  or {}
    m = mvr   or {}
    a = app   or {}

    rows_personal = [
        _cmp("First Name",
             _safe_get(q, "personal", "first_name"),
             _safe_get(d, "personal", "first_name"),
             _safe_get(m, "personal", "first_name"),
             _safe_get(a, "personal", "first_name")),
        _cmp("Last Name",
             _safe_get(q, "personal", "last_name"),
             _safe_get(d, "personal", "last_name"),
             _safe_get(m, "personal", "last_name"),
             _safe_get(a, "personal", "last_name")),
        _cmp("Date of Birth",
             _safe_get(q, "personal", "dob"),
             _safe_get(d, "personal", "dob"),
             _safe_get(m, "personal", "dob"),
             _safe_get(a, "personal", "dob"),
             norm_fn=_norm_date),
        _cmp("Gender",
             _safe_get(q, "personal", "gender"),
             _safe_get(d, "personal", "gender"),
             _safe_get(m, "personal", "gender"),
             _safe_get(a, "personal", "gender")),
        _cmp("Marital Status",
             _safe_get(q, "personal", "marital_status"),
             _safe_get(d, "personal", "marital_status"),
             "",
             _safe_get(a, "personal", "marital_status"),
             sources=("quote", "dash", "app")),
        _cmp("Address",
             _safe_get(q, "personal", "address"),
             _safe_get(d, "personal", "address"),
             _safe_get(m, "personal", "address"),
             _safe_get(a, "personal", "address")),
        _cmp("City",
             _safe_get(q, "personal", "city"),
             "",
             "",
             _safe_get(a, "personal", "city"),
             sources=("quote", "app")),
        _cmp("Postal Code",
             _safe_get(q, "personal", "postal_code"),
             "",
             "",
             _safe_get(a, "personal", "postal_code"),
             sources=("quote", "app")),
        _cmp("Phone",
             _safe_get(q, "personal", "phone"),
             "",
             "",
             _safe_get(a, "personal", "phone"),
             norm_fn=_norm_phone,
             sources=("quote", "app")),
        _cmp("Email",
             _safe_get(q, "personal", "email"),
             "",
             "",
             _safe_get(a, "personal", "email"),
             sources=("quote", "app")),
    ]

    rows_licence = [
        _cmp("Licence Number",
             _safe_get(q, "licence", "licence_number"),
             _safe_get(d, "licence", "licence_number"),
             _safe_get(m, "licence", "licence_number"),
             _safe_get(a, "licence", "licence_number"),
             norm_fn=_norm_licence),
        _cmp("Licence Class",
             _safe_get(q, "licence", "licence_class"),
             _safe_get(d, "licence", "licence_class"),
             _safe_get(m, "licence", "licence_class"),
             _safe_get(a, "licence", "licence_class")),
        _cmp("G Date",
             _safe_get(q, "licence", "g_date"),
             "",
             _safe_get(m, "licence", "g_date"),
             _safe_get(a, "licence", "g_date"),
             norm_fn=_norm_date,
             sources=("quote", "mvr", "app")),
        _cmp("G2 Date",
             _safe_get(q, "licence", "g2_date"),
             "",
             _safe_get(m, "licence", "g2_date"),
             _safe_get(a, "licence", "g2_date"),
             norm_fn=_norm_date,
             sources=("quote", "mvr", "app")),
        _cmp("G1 Date",
             _safe_get(q, "licence", "g1_date"),
             "",
             _safe_get(m, "licence", "g1_date"),
             _safe_get(a, "licence", "g1_date"),
             norm_fn=_norm_date,
             sources=("quote", "mvr", "app")),
        _cmp("Licence Expiry",
             "",
             "",
             _safe_get(m, "licence", "expiry_date"),
             "",
             sources=("mvr",)),
        _cmp("Demerit Points",
             "",
             "",
             _safe_get(m, "licence", "demerit_points"),
             "",
             sources=("mvr",)),
    ]

    rows_insurance = [
        _cmp("Date First Insured",
             _safe_get(q, "licence", "date_first_insured") or _safe_get(q, "insurance_history", "date_first_insured"),
             _safe_get(d, "insurance_history", "date_first_insured"),
             "",
             _safe_get(a, "insurance_history", "date_first_insured"),
             norm_fn=_norm_date,
             sources=("quote", "dash", "app")),
        _cmp("Current Carrier",
             _safe_get(q, "insurance_history", "current_carrier"),
             _safe_get(d, "insurance_history", "current_carrier"),
             "",
             _safe_get(a, "insurance_history", "current_carrier"),
             sources=("quote", "dash", "app")),
        _cmp("Date with Current Carrier",
             _safe_get(q, "insurance_history", "date_with_current_carrier"),
             "",
             "",
             _safe_get(a, "insurance_history", "date_with_current_carrier"),
             norm_fn=_norm_date,
             sources=("quote", "app")),
    ]

    # Vehicles — compare first vehicle only (primary)
    q_veh = (q.get("vehicles") or [{}])[0] if q.get("vehicles") else {}
    d_veh = (d.get("vehicles") or [{}])[0] if d.get("vehicles") else {}
    a_veh = (a.get("vehicles") or [{}])[0] if a.get("vehicles") else {}

    rows_vehicle = [
        _cmp("Year",       q_veh.get("year"), d_veh.get("year"),  "", a_veh.get("year"),  sources=("quote", "dash", "app")),
        _cmp("Make",       q_veh.get("make"), d_veh.get("make"),  "", a_veh.get("make"),  sources=("quote", "dash", "app")),
        _cmp("Model",      q_veh.get("model"), d_veh.get("model"), "", a_veh.get("model"), sources=("quote", "dash", "app")),
        _cmp("VIN",        q_veh.get("vin"),  d_veh.get("vin"),   "", a_veh.get("vin"),   sources=("quote", "dash", "app")),
        _cmp("Annual KM",  q_veh.get("annual_km"), d_veh.get("annual_km"), "", a_veh.get("annual_km"),
             norm_fn=_norm_km, sources=("quote", "dash", "app")),
        _cmp("Use",        q_veh.get("use"), d_veh.get("use"), "", a_veh.get("use"), sources=("quote", "dash", "app")),
        _cmp("Purchase Date", q_veh.get("purchase_date"), "", "", a_veh.get("purchase_date"),
             norm_fn=_norm_date, sources=("quote", "app")),
        _cmp("Winter Tires", q_veh.get("winter_tires"), "", "", a_veh.get("winter_tires"),
             norm_fn=_norm_bool, sources=("quote", "app")),
        _cmp("Leased",     q_veh.get("leased"), "", "", a_veh.get("leased"),
             norm_fn=_norm_bool, sources=("quote", "app")),
    ]

    # Claims — build combined list from all sources
    def _normalise_claims(claims_list, source_label):
        result = []
        for c in (claims_list or []):
            result.append({
                "source": source_label,
                "date": c.get("date") or c.get("claim_date") or "",
                "type": c.get("type") or c.get("claim_type") or "",
                "at_fault": c.get("at_fault") or "",
                "amount": c.get("amount") or "",
            })
        return result

    all_claims = (
        _normalise_claims(q.get("claims"), "Quote") +
        _normalise_claims(d.get("claims"), "DASH") +
        _normalise_claims(a.get("claims"), "App")
    )

    # Convictions — build combined list
    def _normalise_convictions(conv_list, source_label):
        result = []
        for c in (conv_list or []):
            result.append({
                "source": source_label,
                "date": c.get("date") or c.get("offence_date") or "",
                "description": c.get("description") or "",
                "conviction_date": c.get("conviction_date") or "",
                "demerit_points": c.get("demerit_points") or "",
            })
        return result

    all_convictions = (
        _normalise_convictions(q.get("convictions"), "Quote") +
        _normalise_convictions(d.get("convictions"), "DASH") +
        _normalise_convictions(m.get("convictions"), "MVR") +
        _normalise_convictions(a.get("convictions"), "App")
    )

    # MVR Suspensions
    all_suspensions = []
    for s in (m.get("suspensions") or []):
        all_suspensions.append({
            "start_date": s.get("start_date") or "",
            "end_date":   s.get("end_date") or "",
            "type":       s.get("type") or "",
            "case_number": s.get("case_number") or "",
        })

    # DASH Lapses
    all_lapses = []
    for lp in (d.get("lapses") or []):
        all_lapses.append({
            "start_date": lp.get("start_date") or "",
            "end_date":   lp.get("end_date") or "",
            "reason":     lp.get("reason") or "",
        })

    # Tally mismatches for summary
    all_rows = rows_personal + rows_licence + rows_insurance + rows_vehicle
    total = len(all_rows)
    mismatches = [r for r in all_rows if r["status"] == "MISMATCH"]
    missing    = [r for r in all_rows if r["status"] == "MISSING"]

    # Flag critical issues (suspension in MVR but nothing in quote/app)
    critical_issues = []
    if all_suspensions:
        for s in all_suspensions:
            critical_issues.append(
                f"Suspension {s['start_date']} – {s['end_date']} ({s['type']}) in MVR — check if disclosed in quote"
            )

    return {
        "sections": {
            "personal":    rows_personal,
            "licence":     rows_licence,
            "insurance":   rows_insurance,
            "vehicle":     rows_vehicle,
        },
        "claims":       all_claims,
        "convictions":  all_convictions,
        "suspensions":  all_suspensions,
        "lapses":       all_lapses,
        "summary": {
            "total_fields": total,
            "match_count":  len([r for r in all_rows if r["status"] == "MATCH"]),
            "mismatch_count": len(mismatches),
            "missing_count":  len(missing),
            "mismatched_fields": [r["field"] for r in mismatches],
            "missing_fields":   [r["field"] for r in missing],
            "critical_issues":  critical_issues,
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def verify_documents(quote_bytes=None, dash_bytes=None, mvr_bytes=None, app_bytes=None):
    """
    Run full extraction + comparison pipeline.

    Each argument is either bytes (PDF content) or None (not uploaded).
    Returns full result dict including extracted data + comparison.
    """
    results = {}
    errors  = {}

    pairs = [
        ("quote",       quote_bytes),
        ("dash",        dash_bytes),
        ("mvr",         mvr_bytes),
        ("application", app_bytes),
    ]

    for doc_type, pdf_bytes in pairs:
        if pdf_bytes is None:
            results[doc_type] = None
            continue
        print(f"[DOC_VERIFIER] Extracting: {doc_type} ...")
        r = extract_from_pdf(pdf_bytes, doc_type)
        if r["success"]:
            results[doc_type] = r["data"]
        else:
            results[doc_type] = None
            errors[doc_type] = r["error"]
            print(f"[DOC_VERIFIER] Error extracting {doc_type}: {r['error']}")

    comparison = compare_extractions(
        results.get("quote"),
        results.get("dash"),
        results.get("mvr"),
        results.get("application"),
    )

    return {
        "extracted": results,
        "comparison": comparison,
        "extraction_errors": errors,
    }
