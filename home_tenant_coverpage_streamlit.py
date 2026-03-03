import streamlit as st
import streamlit.components.v1 as components
from io import BytesIO
import json, re, os, html as _html
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '.env.local'))

# --- VERTEX AI (optional) ---
try:
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part
    VERTEX_AVAILABLE = True
except ImportError:
    VERTEX_AVAILABLE = False

# --- PDF GENERATION ---
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, Paragraph,
        Spacer, Table, TableStyle, HRFlowable, KeepTogether, PageBreak
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Home & Tenant Coverage Summary - InsLyf",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# --- CUSTOM CSS ---
st.markdown("""
<style>
    header {visibility: hidden;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .section-title {
        font-size: 0.75rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        color: #94a3b8;
        border-bottom: 1px solid #e2e8f0;
        padding-bottom: 0.5rem;
        margin-bottom: 1rem;
        margin-top: 1rem;
    }
    .metric-label {
        font-size: 0.65rem;
        font-weight: 700;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.1em;
    }
    .metric-val {
        font-size: 1.1rem;
        font-weight: 600;
        color: #0f172a;
    }
    div[data-testid="stDownloadButton"] button {
        background-color: #0f172a;
        color: white;
        border: none;
        padding: 0.6rem 1.4rem;
        font-size: 0.9rem;
        font-weight: 600;
        border-radius: 6px;
        letter-spacing: 0.03em;
    }
    div[data-testid="stDownloadButton"] button:hover {
        background-color: #1e293b;
    }
</style>
""", unsafe_allow_html=True)

# --- DEFAULT DATA ---
client_info = {
    "name": "MOHAMED YUSUF LIBAN",
    "address": "604-600 GREENFIELD AVENUE\nKITCHENER, ON N2C 2J9",
    "mobile": "(416) 902-7022",
    "email": "1.yusuf77@gmail.com"
}

broker_info = {
    "brokerage": "KMI Brokers Inc. InsLyf Branch",
    "address": "1321 Matheson Blvd East\nMississauga, ON L4W 0C2",
    "preparedBy": "Eldho George",
    "email": "eldho.george@kmibrokers.com",
    "datePrepared": "01/20/2026"
}

policy_info = {
    "company": "Aviva Insurance Company of Canada",
    "effectiveDate": "02/02/2026",
    "policyType": "Tenant",   # "Tenant", "Homeowners", "Condo"
    "form": "Comprehensive Form"
}

# Properties list  — mirrors the "vehicles" list from auto coverpage.
# Each property has: id, address, type, discounts, coverages
# Special coverage rows with is_header=True render as section dividers in the table.
properties = [
    {
        "id": 1,
        "address": "604-600 GREENFIELD AVENUE\nKITCHENER, ON N2C 2J9",
        "type": "Tenant",
        "discounts": [
            "Claims Free",
            "Multi Line",
            "Mature Policyholder",
            "Continuous Coverage"
        ],
        "coverages": [
            # ── Property Coverages ──
            {"name": "PROPERTY COVERAGES",            "limit": "",              "is_header": True},
            {"name": "Contents (Personal Property)",   "limit": "$35,000"},
            {"name": "Additional Living Expenses",     "limit": "Included"},
            {"name": "All Losses Deductible",          "limit": "$500 Deductible"},
            # ── Liability Coverages ──
            {"name": "LIABILITY COVERAGES",            "limit": "",              "is_header": True},
            {"name": "Personal Liability",             "limit": "$1,000,000"},
            {"name": "Voluntary Medical Payments",     "limit": "$5,000"},
            {"name": "Voluntary Property Damage",      "limit": "$1,000"},
            # ── Optional / Endorsements ──
            {"name": "OPTIONAL COVERAGES",             "limit": "",              "is_header": True},
            {"name": "Sewer Backup",                  "limit": "$15,000"},
            {"name": "Overland Water",                "limit": "$15,000"},
            {"name": "Identity Theft",                "limit": "Included"},
        ]
    }
]

# Default data for Homeowners — used as a reference / secondary sample:
# (Not active by default; shown here so AI can populate it correctly)
_homeowners_coverages_sample = [
    {"name": "PROPERTY COVERAGES",              "limit": "",                      "is_header": True},
    {"name": "Dwelling — Building",             "limit": "$450,000"},
    {"name": "Detached Private Structures",     "limit": "$45,000"},
    {"name": "Contents (Personal Property)",    "limit": "$225,000"},
    {"name": "Additional Living Expenses",      "limit": "Included"},
    {"name": "All Losses Deductible",           "limit": "$1,000 Deductible"},
    {"name": "LIABILITY COVERAGES",             "limit": "",                      "is_header": True},
    {"name": "Personal Liability",              "limit": "$1,000,000"},
    {"name": "Voluntary Medical Payments",      "limit": "$5,000"},
    {"name": "Voluntary Property Damage",       "limit": "$1,000"},
    {"name": "OPTIONAL COVERAGES",              "limit": "",                      "is_header": True},
    {"name": "Sewer Backup",                    "limit": "$50,000"},
    {"name": "Overland Water",                  "limit": "$25,000"},
    {"name": "Identity Theft",                  "limit": "Included"},
    {"name": "Replacement Cost — Contents",     "limit": "Included"},
    {"name": "Earthquake",                      "limit": "$1,000 Deductible"},
]

# =============================================================================
# ENDORSEMENT DESCRIPTIONS
# =============================================================================
ENDORSEMENTS = [
    {
        "title": "Guaranteed Building Replacement Cost",
        "bullets": [
            "Guarantees the insured home will be repaired or rebuilt to its full value at the time of loss, regardless of the coverage amount carried on the policy.",
            "No Coverage Gap — Even if construction costs have risen since the policy was issued, the insurer will cover the full rebuild cost.",
            "Available for homeowners policies — particularly important in markets where rebuilding costs can exceed the stated policy limit.",
        ]
    },
    {
        "title": "Replacement Cost — Contents",
        "bullets": [
            "Offers extra coverage to protect personal possessions such as televisions, furniture, clothing, and appliances.",
            "No Depreciation — Covers the full cost to replace your personal property at today's retail price if damaged or destroyed by a covered loss.",
            "New for Old — A 5-year-old TV destroyed in a fire would be replaced with a comparable new model, not paid out at depreciated value.",
            "Applies to clothing, furniture, electronics, appliances and most household contents.",
        ]
    },
    {
        "title": "Rental Income Protection",
        "bullets": [
            "Covers loss of rental income if a covered loss (such as fire or water damage) renders the insured property uninhabitable.",
            "Protects landlords from lost revenue while the property is being repaired or rebuilt.",
            "Typically covers a set period or dollar amount of rental income as specified in the policy.",
        ]
    },
    {
        "title": "Non-Smoker Discount",
        "bullets": [
            "A premium discount available to households where no occupant has smoked tobacco or other substances within the home for a qualifying period.",
            "Reflects the reduced risk of fire-related losses associated with non-smoking households.",
        ]
    },
    # ── Water Coverages ──────────────────────────────────────────────────────
    {
        "title": "Sewer Back-up",
        "bullets": [
            "Covers water damage caused by the accidental backup or escape of water from a sewer, sump, or septic tank.",
            "Includes costs to repair or replace damaged contents, flooring, walls, and any other affected property.",
            "Standard home and tenant policies do NOT include sewer backup — this endorsement is strongly recommended.",
            "Clean-Up Costs — Covers water removal, drying, and decontamination following a backup event.",
        ]
    },
    {
        "title": "Overland Water",
        "bullets": [
            "Covers the sudden overflow of water sources such as creeks, rivers, and lakes onto normally dry land — sometimes referred to as 'flood'.",
            "Protects against water that enters your home above ground level during heavy rain, rapid snowmelt, or rising water tables.",
            "This coverage is NOT included in a standard policy and must be added as an endorsement.",
        ]
    },
    {
        "title": "Surface Water",
        "bullets": [
            "Covers the sudden and rapid accumulation of water on normally dry surfaces such as driveways, sidewalks, and lawns.",
            "Applies when rainwater or runoff accumulates faster than the ground can absorb it and begins to enter the home.",
            "Distinct from overland water (which comes from a body of water) — surface water comes from rainfall or snowmelt pooling on the ground.",
        ]
    },
    {
        "title": "Ground Water",
        "bullets": [
            "Covers the sudden and accidental infiltration of water through foundations from underground water sources.",
            "Applies when subsurface water pressure forces moisture or water through basement walls, floors, or foundation cracks.",
            "Separate from sewer backup — ground water seeps in from the soil rather than backing up through a drain or sump.",
        ]
    },
    {
        "title": "Sewer Lines / Service Lines",
        "bullets": [
            "Covers underground service lines linking the insured property to municipally or privately maintained services.",
            "Included Lines — Water and sewer lines, electrical conduit, telecommunications conduit, and similar buried infrastructure.",
            "Covers repair or replacement costs if a covered service line is damaged due to a sudden and accidental cause.",
            "Standard policies do not cover service line repairs — this endorsement fills a common and costly coverage gap.",
        ]
    },
]

# =============================================================================
# VERTEX AI EXTRACTION PROMPT — Property / Tenant / Homeowners
# =============================================================================
EXTRACTION_PROMPT = """
You are an insurance data extraction assistant. You will receive an Ontario home, tenant, or condo insurance quote PDF.

Your job is to extract data needed for a COVERPAGE SUMMARY — NOT the full quote.
Do NOT extract premiums, payment schedules, tax amounts, or underwriting notes.

Extract the following and return ONLY a valid JSON object with this exact schema:

{
  "client_info": {
    "name": "FULL NAME IN CAPS",
    "address": "Street address line\\nCity, Province PostalCode",
    "mobile": "(xxx) xxx-xxxx",
    "email": "email@example.com"
  },
  "policy_info": {
    "company": "Full insurance company name",
    "effectiveDate": "MM/DD/YYYY",
    "policyType": "Tenant or Homeowners or Condo",
    "form": "Policy form name e.g. Comprehensive Form, Broad Form, Basic Form"
  },
  "properties": [
    {
      "id": 1,
      "address": "Insured property address",
      "type": "Tenant or Homeowners or Condo",
      "discounts": [
        "Clean discount name only. Remove percentage values and codes. Examples: Claims Free, Multi Line, Mature Policyholder, Continuous Coverage, Mortgage Free, new Purchaser, Winter Credit."
      ],
      "coverages": [
        {
          "name": "Coverage name — see COVERAGE NAMES GUIDE below",
          "limit": "Formatted dollar amount or deductible — see FORMATTING RULES below",
          "is_header": false
        }
      ]
    }
  ]
}

COVERAGE NAMES GUIDE — use these exact names:

For TENANT insurance, include these section headers and coverages IN THIS ORDER:
1. Section header: name="PROPERTY COVERAGES", limit="", is_header=true
   - "Contents (Personal Property)" — the dollar amount of contents coverage
   - "Additional Living Expenses" — usually "Included" or a dollar limit
   - "All Losses Deductible" — the base deductible amount
2. Section header: name="LIABILITY COVERAGES", limit="", is_header=true
   - "Personal Liability" — dollar limit
   - "Voluntary Medical Payments" — dollar limit
   - "Voluntary Property Damage" — dollar limit
3. Section header: name="OPTIONAL COVERAGES", limit="", is_header=true
   - Include any of: "Sewer Backup", "Overland Water", "Identity Theft", "Replacement Cost — Contents", "Home Business", "Scheduled Articles", "Earthquake"
   - Only include optional coverages that are explicitly quoted/listed in the document

For HOMEOWNERS insurance, include these section headers and coverages IN THIS ORDER:
1. Section header: name="PROPERTY COVERAGES", limit="", is_header=true
   - "Dwelling — Building" — replacement cost amount
   - "Detached Private Structures" — dollar amount (usually 10% of building)
   - "Contents (Personal Property)" — dollar amount
   - "Additional Living Expenses" — usually "Included" or a dollar limit
   - "All Losses Deductible" — the base deductible amount
2. Section header: name="LIABILITY COVERAGES", limit="", is_header=true
   - "Personal Liability"
   - "Voluntary Medical Payments"
   - "Voluntary Property Damage"
3. Section header: name="OPTIONAL COVERAGES", limit="", is_header=true
   - Include any explicitly quoted optional coverages

For CONDO insurance, include these section headers and coverages IN THIS ORDER:
1. Section header: name="PROPERTY COVERAGES", limit="", is_header=true
   - "Unit Improvements & Betterments" — dollar amount
   - "Contents (Personal Property)" — dollar amount
   - "Additional Living Expenses" — usually "Included" or a dollar limit
   - "Common Elements / Condo Corporation Deductible" — dollar amount
   - "All Losses Deductible" — the base deductible amount
2. Section header: name="LIABILITY COVERAGES", limit="", is_header=true
   - "Personal Liability"
   - "Voluntary Medical Payments"
   - "Voluntary Property Damage"
3. Section header: name="OPTIONAL COVERAGES", limit="", is_header=true
   - Include any explicitly quoted optional coverages

COVERAGE LIMIT FORMATTING — YOU MUST follow these rules exactly:
- Always use full dollar amounts with dollar sign and commas: "$35,000" NOT "35 K" or "35000"
- For deductibles, append the word Deductible: "$500 Deductible", "$1,000 Deductible"
- For "Included" coverages: use the exact word "Included"
- For million-dollar limits like "1 M" or "1M": convert to "$1,000,000"
- Liability limits are NOT deductibles: "$1,000,000" (no "Deductible" suffix)
- For Voluntary Medical Payments like "$5,000": just write "$5,000" (no Deductible suffix)
- For Voluntary Property Damage like "$1,000": just write "$1,000" (no Deductible suffix)
- For Optional coverages with a limit: format as dollar amount e.g. "$15,000", "$50,000"
- For Optional coverages with a deductible: append "Deductible" e.g. "$1,000 Deductible"
- If a coverage is included at no additional cost: use "Included"
- If a coverage value says "20% of contents" or similar: write it as "Included" unless a specific dollar amt is given

DISCOUNT FORMATTING:
- Extract ONLY the discount NAME (no percentage or code)
- Examples: "Claims Free", "Multi Line", "Multi Policy", "Mature Policyholder", "Mortgage Free", "New Purchaser"

OTHER RULES:
- is_header: true ONLY for section divider rows (PROPERTY COVERAGES, LIABILITY COVERAGES, OPTIONAL COVERAGES)
- is_header: false (or omit) for all actual coverage rows
- If a field is missing, use "" or []
- Do NOT include premiums, costs, taxes, or payment details
- Return ONLY the JSON — no markdown, no explanation, no code fences
"""


def _normalise_property(p: dict, idx: int) -> dict:
    """Ensure a property dict from AI extraction has all required keys with safe defaults."""
    return {
        "id":       p.get("id", idx + 1),
        "address":  p.get("address", f"Property {idx + 1}"),
        "type":     p.get("type", "Tenant"),
        "discounts": [str(d) for d in p.get("discounts", []) if d],
        "coverages": [
            {
                "name":      str(c.get("name", "")),
                "limit":     str(c.get("limit", "")),
                "is_header": bool(c.get("is_header", False)),
            }
            for c in p.get("coverages", [])
            if c.get("name")
        ],
    }


def extract_from_quote_pdf(pdf_bytes: bytes) -> dict | None:
    """Send PDF to Vertex AI Gemini and return parsed + normalised extraction dict."""
    if not VERTEX_AVAILABLE:
        st.error("vertexai package not installed. Run: pip install google-cloud-aiplatform")
        return None
    project_id = os.getenv('GOOGLE_CLOUD_PROJECT')
    location   = os.getenv('GOOGLE_CLOUD_LOCATION', 'us-central1')
    if not project_id:
        st.error("GOOGLE_CLOUD_PROJECT environment variable is not set.")
        return None
    raw = ""
    try:
        vertexai.init(project=project_id, location=location)
        model    = GenerativeModel("gemini-2.0-flash")
        pdf_part = Part.from_data(data=pdf_bytes, mime_type="application/pdf")
        response = model.generate_content([EXTRACTION_PROMPT, pdf_part])
        raw = response.text.strip()
        # Strip markdown code fences if model wraps output anyway
        if raw.startswith("```json"): raw = raw[7:]
        if raw.startswith("```"):     raw = raw[3:]
        if raw.endswith("```"):       raw = raw[:-3]
        data = json.loads(raw.strip())
        # Normalise properties
        data["properties"] = [
            _normalise_property(p, i) for i, p in enumerate(data.get("properties", []))
        ]
        return data
    except json.JSONDecodeError as e:
        st.error(f"Could not parse Vertex AI response as JSON: {e}")
        st.code(raw[:2000])
        return None
    except Exception as e:
        st.error(f"Vertex AI error: {e}")
        return None


# =============================================================================
# PDF GENERATION — ReportLab letter layout
# =============================================================================
def generate_pdf(client_info, broker_info, policy_info, properties):
    buf = BytesIO()

    # ── Colour palette ────────────────────────────────────────────────────────
    C_INK      = colors.HexColor("#0f172a")
    C_DARK     = colors.HexColor("#1e293b")
    C_MID      = colors.HexColor("#334155")
    C_MUTED    = colors.HexColor("#64748b")
    C_LIGHT    = colors.HexColor("#94a3b8")
    C_BORDER   = colors.HexColor("#e2e8f0")
    C_SURFACE  = colors.HexColor("#f8fafc")
    C_GREEN_BG = colors.HexColor("#ecfdf5")
    C_GREEN_FG = colors.HexColor("#047857")
    C_GREEN_BD = colors.HexColor("#a7f3d0")
    C_BLUE     = colors.HexColor("#1d4ed8")
    C_COV_HDR  = colors.HexColor("#1e293b")
    C_ACCENT   = colors.HexColor("#0369a1")   # Blue-teal accent for property (distinct from auto)
    C_SEC_HDR  = colors.HexColor("#334155")   # Section header row bg within coverage table
    C_SEC_TXT  = colors.HexColor("#94a3b8")   # Section header text colour

    PAGE_W, PAGE_H = letter
    MARGIN_X   = 0.65 * inch
    MARGIN_TOP = 0.60 * inch
    MARGIN_BOT = 0.60 * inch
    BODY_W     = PAGE_W - 2 * MARGIN_X

    # ── Style factory ─────────────────────────────────────────────────────────
    def S(name, **kw):
        defaults = dict(fontName="Helvetica", fontSize=9, leading=13,
                        textColor=C_INK, spaceAfter=0, spaceBefore=0)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    sNormal   = S("normal")
    sSub      = S("sub",   fontName="Helvetica-Bold", fontSize=7,    textColor=C_MUTED,  leading=10, letterSpacing=1.2)
    sTitle    = S("title", fontName="Helvetica",      fontSize=22,   textColor=C_INK,    leading=26)
    sPrepLbl  = S("plbl",  fontName="Helvetica-Bold", fontSize=6.5,  textColor=C_LIGHT,  leading=9, alignment=TA_RIGHT)
    sPrepName = S("pname", fontName="Helvetica-Bold", fontSize=12,   textColor=C_DARK,   leading=15, alignment=TA_RIGHT)

    sRibLbl   = S("rl",  fontName="Helvetica-Bold", fontSize=6.5, textColor=C_MUTED,  leading=9)
    sRibVal   = S("rv",  fontName="Helvetica-Bold", fontSize=10,  textColor=C_INK,    leading=13)
    sRibValB  = S("rvb", fontName="Helvetica-Bold", fontSize=10,  textColor=C_BLUE,   leading=13)

    sPanelHead= S("ph",  fontName="Helvetica-Bold", fontSize=9,   textColor=C_INK)
    sPanelAddr= S("pa",  fontSize=8.5,              textColor=C_MID,  leading=12)
    sPanelCont= S("pc",  fontSize=8.5,              textColor=C_INK,  leading=12)

    sSecLabel = S("sl",  fontName="Helvetica-Bold", fontSize=7,   textColor=C_MUTED,  leading=10)
    sFooter   = S("ft",  fontSize=7.5,              textColor=C_MUTED, leading=11)

    sDiscInline = S("di", fontSize=8.5, textColor=C_MID, leading=13, fontName="Helvetica")

    sPropNum  = S("vn",  fontName="Helvetica-Bold", fontSize=7,   textColor=colors.white, leading=10)
    sPropDesc = S("vd",  fontName="Helvetica-Bold", fontSize=11,  textColor=colors.white, leading=14)

    sCovHdrL  = S("chl", fontName="Helvetica-Bold", fontSize=7,   textColor=colors.white, leading=10)
    sCovHdrR  = S("chr", fontName="Helvetica-Bold", fontSize=7,   textColor=colors.white, leading=10, alignment=TA_RIGHT)
    sCovName  = S("cn",  fontName="Helvetica-Bold", fontSize=9,   textColor=C_DARK,   leading=12)
    sCovLimit = S("cl",  fontName="Helvetica-Bold", fontSize=8.5, textColor=C_INK,    leading=11, alignment=TA_RIGHT)
    sCovInc   = S("ci",  fontName="Helvetica-Bold", fontSize=8.5, textColor=C_GREEN_FG, leading=11, alignment=TA_RIGHT)
    sCovSec   = S("cs",  fontName="Helvetica-Bold", fontSize=6.5, textColor=C_SEC_TXT, leading=9,
                          letterSpacing=1.5)

    # ── Document setup ────────────────────────────────────────────────────────
    frame = Frame(MARGIN_X, MARGIN_BOT, BODY_W, PAGE_H - MARGIN_TOP - MARGIN_BOT,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(buf, pagesize=letter,
                          leftMargin=MARGIN_X, rightMargin=MARGIN_X,
                          topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOT)
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])
    story = []

    # ── Helpers ───────────────────────────────────────────────────────────────
    def section_rule(label, width=None):
        w = width or BODY_W
        t = Table([[Paragraph(label.upper(), sSecLabel)]],
                  colWidths=[w], rowHeights=[13])
        t.setStyle(TableStyle([
            ("LINEBELOW",     (0,0),(-1,-1), 0.75, C_BORDER),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("TOPPADDING",    (0,0),(-1,-1), 0),
            ("LEFTPADDING",   (0,0),(-1,-1), 0),
            ("RIGHTPADDING",  (0,0),(-1,-1), 0),
        ]))
        return t

    def footer_block():
        sig_w = 2.4 * inch
        sig_tbl = Table(
            [[Paragraph("", sNormal)],
             [HRFlowable(width=sig_w, thickness=0.5, color=C_LIGHT)],
             [Paragraph("CLIENT SIGNATURE",
                        S("sig", fontName="Helvetica-Bold", fontSize=6.5,
                          textColor=C_LIGHT, leading=9, alignment=TA_CENTER))]],
            colWidths=[sig_w], rowHeights=[18, 3, 12]
        )
        ft = Table([[
            [Paragraph("<b>KMI Brokers Inc. — InsLyf Branch</b>", sFooter),
             Paragraph("This document is a coverage summary only and does not constitute"
                       " a binding contract of insurance.", sFooter)],
            sig_tbl,
        ]], colWidths=[BODY_W - sig_w - 20, sig_w + 20])
        ft.setStyle(TableStyle([
            ("VALIGN",       (0,0),(-1,-1), "BOTTOM"),
            ("LEFTPADDING",  (0,0),(-1,-1), 0),
            ("RIGHTPADDING", (0,0),(-1,-1), 0),
            ("TOPPADDING",   (0,0),(-1,-1), 0),
            ("BOTTOMPADDING",(0,0),(-1,-1), 0),
            ("ALIGN",        (1,0),(1, 0),  "RIGHT"),
        ]))
        return ft

    # ── Derive label ──────────────────────────────────────────────────────────
    _policy_type = policy_info.get("policyType", "Home")
    _type_upper  = _policy_type.upper()
    _subtitle    = {
        "Tenant":     "TENANT INSURANCE",
        "Homeowners": "HOMEOWNERS INSURANCE",
        "Condo":      "CONDO INSURANCE",
    }.get(_policy_type, "HOME INSURANCE")

    # ═══════════════════════════════════════════════════════════════════════════
    # 1.  HEADER
    # ═══════════════════════════════════════════════════════════════════════════
    hdr = Table([[
        [Paragraph(_subtitle, sSub),
         Paragraph("Coverage Summary", sTitle)],
        [Paragraph("PREPARED FOR", sPrepLbl),
         Paragraph(client_info["name"], sPrepName)]
    ]], colWidths=[BODY_W * 0.55, BODY_W * 0.45])
    hdr.setStyle(TableStyle([
        ("VALIGN",       (0,0),(-1,-1), "BOTTOM"),
        ("LEFTPADDING",  (0,0),(-1,-1), 0),
        ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING",   (0,0),(-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("RIGHTPADDING", (0,0),(0,-1),  16),
        ("LEFTPADDING",  (1,0),(1,-1),  16),
    ]))
    story.append(hdr)
    story.append(HRFlowable(width=BODY_W, thickness=2, color=C_ACCENT, spaceAfter=0))

    # ═══════════════════════════════════════════════════════════════════════════
    # 2.  RIBBON
    # ═══════════════════════════════════════════════════════════════════════════
    col_w = BODY_W / 4
    ribbon = Table([[
        [Paragraph("PROPOSED EFF. DATE", sRibLbl),
         Paragraph(policy_info["effectiveDate"], sRibVal)],
        [Paragraph("INSURANCE PROVIDER", sRibLbl),
         Paragraph(policy_info["company"], sRibVal)],
        [Paragraph("POLICY TYPE", sRibLbl),
         Paragraph(_policy_type, sRibVal)],
        [Paragraph("STATUS", sRibLbl),
         Paragraph("Pending Binding", sRibValB)],
    ]], colWidths=[col_w] * 4, rowHeights=[40])
    ribbon.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), C_SURFACE),
        ("LINEABOVE",    (0,0),(-1, 0), 1,   C_ACCENT),
        ("LINEBELOW",    (0,0),(-1,-1), 0.5, C_BORDER),
        ("LINEAFTER",    (0,0),(2,-1),  0.5, C_BORDER),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ("LEFTPADDING",  (0,0),(-1,-1), 14),
        ("RIGHTPADDING", (0,0),(-1,-1), 10),
        ("TOPPADDING",   (0,0),(-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ]))
    story.append(ribbon)
    story.append(Spacer(1, 16))

    # ═══════════════════════════════════════════════════════════════════════════
    # 3.  POLICYHOLDER + BROKERAGE PANELS
    # ═══════════════════════════════════════════════════════════════════════════
    GAP     = 0.14 * inch
    panel_w = (BODY_W - GAP) / 2

    def build_panel(rows, width):
        tbl_rows, styles, heights = [], [
            ("BOX",           (0,0),(-1,-1), 0.5,  C_BORDER),
            ("BACKGROUND",    (0,0),(-1,-1), colors.white),
            ("LEFTPADDING",   (0,0),(-1,-1), 12),
            ("RIGHTPADDING",  (0,0),(-1,-1), 12),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
            ("TOPPADDING",    (0,0),(0, 0),  12),
            ("BOTTOMPADDING", (0,0),(0, 0),  6),
        ], []
        for i, row in enumerate(rows):
            if row == ["__divider__"]:
                tbl_rows.append([HRFlowable(width=width - 24, thickness=0.5, color=C_BORDER)])
                styles += [("TOPPADDING",    (0,i),(0,i), 6),
                           ("BOTTOMPADDING", (0,i),(0,i), 6)]
                heights.append(14)
            else:
                tbl_rows.append(row)
                heights.append(None)
        t = Table(tbl_rows, colWidths=[width], rowHeights=heights)
        t.setStyle(TableStyle(styles))
        return t

    def rows_client():
        r = [[Paragraph(client_info["name"], sPanelHead)]]
        for ln in client_info["address"].split("\n"):
            r.append([Paragraph(ln, sPanelAddr)])
        r.append(["__divider__"])
        r.append([Paragraph(
            f'<font color="#94a3b8">Tel \u2002</font>'
            f'<b>{client_info["mobile"]}</b>', sPanelCont)])
        r.append([Paragraph(
            f'<font color="#94a3b8">Email </font>'
            f'{client_info["email"]}', sPanelCont)])
        return r

    def rows_broker():
        r = [[Paragraph(broker_info["brokerage"], sPanelHead)]]
        for ln in broker_info["address"].split("\n"):
            r.append([Paragraph(ln, sPanelAddr)])
        r.append(["__divider__"])
        r.append([Paragraph(
            f'<font color="#94a3b8">Broker </font>'
            f'<b>{broker_info["preparedBy"]}</b>', sPanelCont)])
        r.append([Paragraph(
            f'<font color="#94a3b8">Email \u2002</font>'
            f'{broker_info["email"]}', sPanelCont)])
        return r

    panels = Table(
        [[build_panel(rows_client(), panel_w),
          Spacer(GAP, 1),
          build_panel(rows_broker(), panel_w)]],
        colWidths=[panel_w, GAP, panel_w]
    )
    panels.setStyle(TableStyle([
        ("LEFTPADDING",  (0,0),(-1,-1), 0), ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING",   (0,0),(-1,-1), 0), ("BOTTOMPADDING",(0,0),(-1,-1), 0),
        ("VALIGN",       (0,0),(-1,-1), "TOP"),
    ]))
    story.append(panels)

    # ═══════════════════════════════════════════════════════════════════════════
    # 4.  PROPERTIES
    # ═══════════════════════════════════════════════════════════════════════════
    for idx, prop in enumerate(properties):
        if idx == 0:
            story.append(Spacer(1, 18))
        else:
            story.append(PageBreak())

        # ── Property header band ──────────────────────────────────────────────
        prop_type_label = prop.get("type", _policy_type).upper()
        prop_addr_lines = prop["address"].replace("\n", "  \u2022  ")
        phdr = Table([[
            Paragraph(f"{prop_type_label} POLICY", sPropNum),
            Paragraph(prop_addr_lines, sPropDesc),
        ]], colWidths=[80, BODY_W - 80], rowHeights=[36])
        phdr.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,-1), C_INK),
            ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
            ("LEFTPADDING",  (0,0),(-1,-1), 14),
            ("RIGHTPADDING", (0,0),(-1,-1), 14),
            ("TOPPADDING",   (0,0),(-1,-1), 0),
            ("BOTTOMPADDING",(0,0),(-1,-1), 0),
            ("LINEABOVE",    (0,0),(-1, 0), 2, C_ACCENT),
        ]))
        story.append(phdr)
        story.append(Spacer(1, 10))

        # ── Applied Discounts ─────────────────────────────────────────────────
        if prop.get("discounts"):
            story.append(section_rule("Applied Discounts"))
            story.append(Spacer(1, 5))
            disc_html = "   ".join(
                f'<font color="#0369a1"><b>\u2713</b></font> {d}'
                for d in prop["discounts"]
            )
            story.append(Paragraph(disc_html, sDiscInline))
            story.append(Spacer(1, 8))

        # ── Schedule of Coverages ─────────────────────────────────────────────
        story.append(section_rule("Schedule of Coverages"))
        story.append(Spacer(1, 5))

        cov_col1 = BODY_W * 0.65
        cov_col2 = BODY_W * 0.35

        # Build table rows — section headers get a special rendering
        cov_data = [[
            Paragraph("COVERAGE DESCRIPTION", sCovHdrL),
            Paragraph("LIMIT / DEDUCTIBLE",   sCovHdrR),
        ]]
        cov_is_header_rows = []   # track which data rows are section headers

        non_header_count = 0  # used for alternating stripe (skip header rows)
        for cov in prop["coverages"]:
            if cov.get("is_header"):
                cov_data.append([
                    Paragraph(cov["name"], sCovSec),
                    Paragraph("", sCovSec),
                ])
                cov_is_header_rows.append(True)
                non_header_count = 0  # reset stripe counter at each new section
            else:
                lp = Paragraph(cov["limit"], sCovInc if cov["limit"] == "Included" else sCovLimit)
                cov_data.append([Paragraph(cov["name"], sCovName), lp])
                cov_is_header_rows.append(False)
                non_header_count += 1

        cov_tbl = Table(cov_data, colWidths=[cov_col1, cov_col2])
        cov_styles = [
            # Header row (col headers)
            ("BACKGROUND",    (0,0),(-1, 0),  C_COV_HDR),
            ("BOX",           (0,0),(-1,-1),  0.75, C_BORDER),
            ("LINEBELOW",     (0,0),(-1, 0),  1,    C_ACCENT),
            ("TOPPADDING",    (0,0),(-1, 0),  6),
            ("BOTTOMPADDING", (0,0),(-1, 0),  6),
            ("LEFTPADDING",   (0,0),(-1,-1),  12),
            ("RIGHTPADDING",  (0,0),(-1,-1),  12),
            ("VALIGN",        (0,0),(-1,-1),  "MIDDLE"),
        ]

        stripe_n = 0
        for i, is_hdr in enumerate(cov_is_header_rows):
            row_i = i + 1  # +1 because row 0 is column headers
            if is_hdr:
                # Section header row — slate-100 tint, no stripe
                cov_styles += [
                    ("BACKGROUND",    (0,row_i),(-1,row_i), C_SURFACE),
                    ("LINEABOVE",     (0,row_i),(-1,row_i), 0.75, C_BORDER),
                    ("LINEBELOW",     (0,row_i),(-1,row_i), 0.5,  C_BORDER),
                    ("TOPPADDING",    (0,row_i),(-1,row_i), 5),
                    ("BOTTOMPADDING", (0,row_i),(-1,row_i), 5),
                    ("SPAN",          (0,row_i),(1,row_i)),
                ]
                stripe_n = 0
            else:
                bg = colors.white if stripe_n % 2 == 0 else C_SURFACE
                cov_styles += [
                    ("BACKGROUND",    (0,row_i),(-1,row_i), bg),
                    ("TOPPADDING",    (0,row_i),(-1,row_i), 5),
                    ("BOTTOMPADDING", (0,row_i),(-1,row_i), 4),
                    ("LINEBELOW",     (0,row_i),(-1,row_i), 0.5, C_BORDER),
                ]
                stripe_n += 1

        # "Included" green cells — only on data rows
        for i, (cov, is_hdr) in enumerate(zip(prop["coverages"], cov_is_header_rows)):
            if not is_hdr and cov["limit"] == "Included":
                row_i = i + 1
                cov_styles += [
                    ("BACKGROUND",  (1,row_i),(1,row_i), C_GREEN_BG),
                    ("LINEAFTER",   (1,row_i),(1,row_i), 0.75, C_GREEN_BD),
                    ("LINEBEFORE",  (1,row_i),(1,row_i), 0.5,  C_BORDER),
                ]

        cov_tbl.setStyle(TableStyle(cov_styles))
        story.append(cov_tbl)

        # ── Page footer ───────────────────────────────────────────────────────
        story.append(Spacer(1, 22))
        story.append(HRFlowable(width=BODY_W, thickness=0.75, color=C_BORDER, spaceAfter=8))
        story.append(footer_block())

    # ═══════════════════════════════════════════════════════════════════════════
    # 5.  ENDORSEMENT DESCRIPTIONS — last page
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())

    endo_title_style = S("et", fontName="Helvetica-Bold", fontSize=13, textColor=C_INK, leading=16, spaceAfter=2)
    endo_subtitle    = S("es", fontName="Helvetica", fontSize=7.5, textColor=C_MUTED, leading=10, spaceAfter=12)
    endo_heading     = S("eh", fontName="Helvetica-Bold", fontSize=9, textColor=C_INK, leading=12, spaceBefore=10, spaceAfter=3)
    endo_bullet      = S("eb", fontName="Helvetica", fontSize=7.5, textColor=C_MID, leading=11,
                          leftIndent=12, bulletIndent=0, bulletFontName="Helvetica",
                          bulletFontSize=7.5, bulletColor=C_MUTED)

    story.append(Paragraph("Endorsement Descriptions", endo_title_style))
    story.append(Paragraph(
        "The following endorsements may apply to your policy. "
        "Please refer to your policy documents for full terms and conditions.",
        endo_subtitle
    ))
    story.append(HRFlowable(width=BODY_W, thickness=1, color=C_ACCENT, spaceAfter=6))

    for endo in ENDORSEMENTS:
        story.append(Paragraph(endo["title"], endo_heading))
        for b in endo["bullets"]:
            story.append(Paragraph(f"\u2022  {b}", endo_bullet))
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width=BODY_W, thickness=0.4, color=C_BORDER, spaceAfter=2))

    story.append(Spacer(1, 18))
    story.append(HRFlowable(width=BODY_W, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(footer_block())

    doc.build(story)
    buf.seek(0)
    return buf


# =============================================================================
# STREAMLIT UI
# =============================================================================
_e = _html.escape  # shorthand for XSS-safe HTML escaping

# ── Upload & Extract ─────────────────────────────────────────────────────────
st.markdown("### Upload Quote PDF")
with st.expander("📄 Upload a home / tenant / condo insurance quote to auto-populate the coverpage", expanded=True):
    uploaded = st.file_uploader("Choose a quote PDF", type=["pdf"], label_visibility="collapsed")
    if uploaded:
        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.caption(f"File: **{uploaded.name}**  ({uploaded.size/1024:.1f} KB)")
        with col_b:
            if st.button("Extract with AI", type="primary", use_container_width=True):
                if not VERTEX_AVAILABLE:
                    st.error("Install vertexai: `pip install google-cloud-aiplatform`")
                elif not os.getenv('GOOGLE_CLOUD_PROJECT'):
                    st.error("GOOGLE_CLOUD_PROJECT environment variable is not set.")
                else:
                    with st.spinner("Extracting data from quote…"):
                        result = extract_from_quote_pdf(uploaded.read())
                    if result:
                        st.session_state["extracted_property"] = result
                        st.success("Extraction complete — coverpage updated below.")
                        st.rerun()

st.divider()

# Use extracted data if available, otherwise fall back to defaults
_data = st.session_state.get("extracted_property", {})
if _data:
    _ci = _data.get("client_info") or {}
    client_info = {
        "name":    _ci.get("name",    client_info["name"]),
        "address": _ci.get("address", client_info["address"]),
        "mobile":  _ci.get("mobile",  client_info["mobile"]),
        "email":   _ci.get("email",   client_info["email"]),
    }
    _pi = _data.get("policy_info") or {}
    policy_info = {
        "company":       _pi.get("company",       policy_info["company"]),
        "effectiveDate": _pi.get("effectiveDate",  policy_info["effectiveDate"]),
        "policyType":    _pi.get("policyType",     policy_info["policyType"]),
        "form":          _pi.get("form",           policy_info.get("form", "")),
    }
    _props = _data.get("properties") or []
    if _props:
        properties = _props

_policy_type_ui = policy_info.get("policyType", "Tenant")

if REPORTLAB_AVAILABLE:
    pdf_buf = generate_pdf(client_info, broker_info, policy_info, properties)
    fname = f"{_policy_type_ui}_CoverPage_{client_info['name'].replace(' ', '_') or 'Coverage'}.pdf"
    st.download_button(
        label="Export as PDF",
        data=pdf_buf,
        file_name=fname,
        mime="application/pdf"
    )
else:
    st.warning("Install reportlab to enable PDF export: pip install reportlab")

# ── Header ────────────────────────────────────────────────────────────────────
_type_label_ui = {
    "Tenant":     "TENANT INSURANCE",
    "Homeowners": "HOMEOWNERS INSURANCE",
    "Condo":      "CONDO INSURANCE",
}.get(_policy_type_ui, "HOME INSURANCE")

st.markdown(f"""
<div style="display: flex; justify-content: space-between; align-items: flex-end; padding-bottom: 20px;">
    <div>
        <div style="color: #64748b; letter-spacing: 0.2em; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; margin-bottom: 5px;">{_e(_type_label_ui)}</div>
        <div style="font-size: 2.5rem; font-weight: 300; line-height: 1; color: #0f172a; letter-spacing: -0.02em;">Coverage Summary</div>
    </div>
    <div style="text-align: right; border-right: 2px solid #e2e8f0; padding-right: 15px;">
        <div style="color: #94a3b8; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 2px;">Prepared For</div>
        <div style="font-size: 1.25rem; font-weight: 500; color: #1e293b;">{_e(client_info['name'])}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Ribbon ────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; padding: 15px 20px; display: flex; justify-content: space-between; margin-bottom: 30px;">
    <div><div class="metric-label">Proposed Eff. Date</div><div class="metric-val">{_e(policy_info['effectiveDate'])}</div></div>
    <div><div class="metric-label">Insurance Provider</div><div class="metric-val">{_e(policy_info['company'])}</div></div>
    <div><div class="metric-label">Policy Type</div><div class="metric-val">{_e(_policy_type_ui)}</div></div>
    <div><div class="metric-label">Status</div><div class="metric-val" style="color: #1d4ed8;">Pending Binding</div></div>
</div>
""", unsafe_allow_html=True)

# ── Panels ────────────────────────────────────────────────────────────────────
col1, gap, col2 = st.columns([1, 0.1, 1])
with col1:
    with st.container(border=True):
        st.markdown(f"**{_e(client_info['name'])}**")
        st.markdown(_e(client_info['address']).replace('\n', '<br>'), unsafe_allow_html=True)
        st.divider()
        st.markdown(f"<span style='color:#64748b; width:45px; display:inline-block;'>Tel:</span> {_e(client_info['mobile'])}", unsafe_allow_html=True)
        st.markdown(f"<span style='color:#64748b; width:45px; display:inline-block;'>Email:</span> {_e(client_info['email'])}", unsafe_allow_html=True)

with col2:
    with st.container(border=True):
        st.markdown(f"**{_e(broker_info['brokerage'])}**")
        st.markdown(_e(broker_info['address']).replace('\n', '<br>'), unsafe_allow_html=True)
        st.divider()
        st.markdown(f"<span style='color:#64748b; width:55px; display:inline-block;'>Broker:</span> **{_e(broker_info['preparedBy'])}**", unsafe_allow_html=True)
        st.markdown(f"<span style='color:#64748b; width:55px; display:inline-block;'>Email:</span> {_e(broker_info['email'])}", unsafe_allow_html=True)

st.write("")

# ── Properties (each with grouped coverage table) ─────────────────────────────
for prop in properties:
    _prop_type = _e(prop.get("type", _policy_type_ui))
    _prop_addr = _e(prop.get("address", "")).replace("\n", "  &bull;  ")

    st.markdown(f"""
    <div style="border-top: 2px solid #0f172a; padding-top: 30px; margin-top: 20px; margin-bottom: 20px;">
        <h2 style="font-size: 1.5rem; font-weight: 300; color: #0f172a; margin: 0;">
            <span style="font-weight: 700; color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.15em; margin-right: 10px;">{_prop_type}</span>
            {_prop_addr}
        </h2>
    </div>
    """, unsafe_allow_html=True)

    if prop.get("discounts"):
        st.markdown('<div class="section-title" style="margin-top:0;">Applied Discounts</div>', unsafe_allow_html=True)
        disc_inline = "   ".join(
            f"<span style='color:#0369a1; font-weight:700;'>&#10003;</span>"
            f"<span style='color:#334155; font-size:0.875rem; margin-left:5px;'>{_e(d)}</span>"
            for d in prop["discounts"]
        )
        st.markdown(f"<div style='margin-bottom:12px; font-size:0.875rem; line-height:1.8;'>{disc_inline}</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-title">Schedule of Coverages</div>', unsafe_allow_html=True)

    # Build HTML coverage table with section headers inline
    table_html = """
    <div style="border: 1px solid #e2e8f0; border-radius: 4px; overflow: hidden; margin-bottom: 40px;">
        <table style="width:100%; text-align:left; border-collapse: collapse; font-family: sans-serif; font-size: 0.9rem;">
            <thead style="background-color: #1e293b; border-bottom: 1px solid #e2e8f0;">
                <tr>
                    <th style="padding: 12px 20px; font-weight: 600; color: #ffffff; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; width: 66%;">Coverage Description</th>
                    <th style="padding: 12px 20px; font-weight: 600; color: #ffffff; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; text-align: right; width: 33%;">Limit / Deductible</th>
                </tr>
            </thead>
            <tbody>
    """
    stripe = 0
    for cov in prop["coverages"]:
        if cov.get("is_header"):
            table_html += f"""
                <tr style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0;">
                    <td colspan="2" style="padding: 8px 20px; color: #64748b; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.15em;">{_e(cov['name'])}</td>
                </tr>"""
            stripe = 0
        else:
            row_bg = "#ffffff" if stripe % 2 == 0 else "#f8fafc"
            if cov["limit"] == "Included":
                ls = "background-color: #ecfdf5; color: #047857; padding: 4px 10px; border-radius: 4px; border: 1px solid #d1fae5;"
            else:
                ls = "color: #0f172a;"
            table_html += f"""
                <tr style="border-bottom: 1px solid #f1f5f9; background-color: {row_bg};">
                    <td style="padding: 12px 20px; color: #1e293b; font-weight: 500;">{_e(cov['name'])}</td>
                    <td style="padding: 12px 20px; text-align: right;">
                        <span style="{ls} font-weight: 600; font-size: 0.85rem;">{_e(cov['limit'])}</span>
                    </td>
                </tr>"""
            stripe += 1

    table_html += "</tbody></table></div>"

    # Calculate height: col-headers=46, section-headers=35 each, data-rows=46 each
    _n_data   = sum(1 for c in prop["coverages"] if not c.get("is_header"))
    _n_sec    = sum(1 for c in prop["coverages"] if c.get("is_header"))
    _tbl_h    = 46 + _n_data * 46 + _n_sec * 36 + 10
    components.html(table_html, height=_tbl_h, scrolling=False)

# ── Endorsement Descriptions ──────────────────────────────────────────────────
st.markdown("""<div style="border-top: 2px solid #0f172a; padding-top: 24px; margin-top: 40px;">
    <h3 style="font-size: 1.05rem; font-weight: 600; color: #0f172a; margin: 0 0 4px 0;">Endorsement Descriptions</h3>
    <p style="font-size: 0.72rem; color: #94a3b8; margin: 0 0 12px 0;">The following endorsements may apply to your policy. Please refer to your policy documents for full terms and conditions.</p>
</div>""", unsafe_allow_html=True)

for endo in ENDORSEMENTS:
    st.markdown(f"""
    <div style="margin-bottom: 14px;">
        <div style="font-size: 0.82rem; font-weight: 700; color: #0f172a; margin-bottom: 5px;">{_e(endo['title'])}</div>
        {''.join(f'<div style="font-size: 0.75rem; color: #475569; line-height: 1.6; padding-left: 12px;">\u2022&ensp;{b}</div>' for b in endo['bullets'])}
    </div>
    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 0;">
    """, unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="border-top: 2px solid #0f172a; padding-top: 30px; margin-top: 60px; display: flex; justify-content: space-between; align-items: center; font-family: sans-serif; margin-bottom: 50px;">
    <div style="font-size: 0.85rem; color: #64748b;">
        <strong style="color: #334155;">KMI Brokers Inc. InsLyf Branch</strong><br/>
        This document is a coverage summary and does not constitute a binding contract.
    </div>
    <div style="width: 250px; border-bottom: 1px solid #94a3b8; padding-bottom: 5px; text-align: center; text-transform: uppercase; letter-spacing: 0.1em; color: #94a3b8; font-size: 0.7rem; font-weight: bold;">
        Client Signature
    </div>
</div>
""", unsafe_allow_html=True)
