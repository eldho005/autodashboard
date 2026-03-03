import streamlit as st
import streamlit.components.v1 as components
from io import BytesIO
import json, re, os, html as _html
from dotenv import load_dotenv

# Load environment variables (same .env.local as property coverpage)
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
    page_title="Coverage Summary - InsLyf",
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

# --- DATA ---
client_info = {
    "name": "MOHAMED YUSUF LIBAN",
    "address": "1504-600 GREENFIELD AVENUE\nKITCHENER, ON N2C 2J9",
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
    "company": "Optimum Insurance Company",
    "effectiveDate": "02/02/2026"
}

vehicles = [
    {
        "id": 1,
        "description": "2025 NISSAN ROGUE S 4DR AWD",
        "discounts": [
            "Minor Conviction Free",
            "Multi Line",
            "Mature Driver",
            "Winter Tire - Veh. equipped with 4 approved winter tires from Dec. - Apr."
        ],
        "coverages": [
            { "name": "Bodily Injury", "limit": "$2,000,000" },
            { "name": "Property Damage", "limit": "$2,000,000" },
            { "name": "Direct Compensation", "limit": "$0 Deductible" },
            { "name": "Accident Benefits", "limit": "Included" },
            { "name": "All Perils", "limit": "$1,000 Deductible" },
            { "name": "Uninsured Automobile", "limit": "Included" },
            { "name": "Loss of Use (OPCF 20)", "limit": "$1,500" },
            { "name": "Mortgage (OPCF 23a)", "limit": "Included" },
            { "name": "Limited Waiver of Depreciation (OPCF 43)", "limit": "24 Months" },
            { "name": "Family Protection (OPCF 44)", "limit": "$2,000,000" },
            { "name": "Accident Waiver", "limit": "Included" }
        ]
    },
    {
        "id": 2,
        "description": "2020 HONDA CIVIC EX",
        "discounts": [
            "Multi Line",
            "Winter Tire - Veh. equipped with 4 approved winter tires from Dec. - Apr."
        ],
        "coverages": [
            { "name": "Bodily Injury", "limit": "$1,000,000" },
            { "name": "Property Damage", "limit": "$1,000,000" },
            { "name": "Direct Compensation", "limit": "$500 Deductible" },
            { "name": "Accident Benefits", "limit": "Included" },
            { "name": "Comprehensive", "limit": "$500 Deductible" },
            { "name": "Collision", "limit": "$500 Deductible" },
            { "name": "Uninsured Automobile", "limit": "Included" },
            { "name": "Family Protection (OPCF 44)", "limit": "$1,000,000" }
        ]
    }
]


# =============================================================================
# ENDORSEMENT DESCRIPTIONS (appended after last vehicle page)
# =============================================================================
ENDORSEMENTS = [
    {
        "title": "Accident Forgiveness (OPCF 39)",
        "bullets": [
            "Accident Forgiveness is a valuable feature commonly known as Accident Waiver.",
            "First At-Fault Accident \u2014 Maintain your current premium rate without an increase.",
            "Premium Protection \u2014 Protects your premium from rising due to your first at-fault accident.",
            "Renewal Rate \u2014 Without this waiver, your renewal rate could significantly increase after an at-fault accident.",
        ]
    },
    {
        "title": "Loss of Use (OPCF 20)",
        "bullets": [
            "Covers costs for alternative transportation if your vehicle is unavailable due to a covered claim.",
            "Rental Vehicle \u2014 Pays for a rental car while your vehicle is being repaired.",
            "Public Transportation \u2014 Covers buses, trains and other public transit costs.",
            "Taxis \u2014 Includes the expense of hiring taxis.",
            "Combination \u2014 You may use a mix of the above methods and costs will be covered.",
        ]
    },
    {
        "title": "Non-Owned Automobile (OPCF 27)",
        "bullets": [
            "Provides a transfer of coverage from your current policy to a rental vehicle with applicable deductible.",
        ]
    },
    {
        "title": "Waiver of Depreciation (OPCF 43)",
        "bullets": [
            "Waives depreciation on a new vehicle in the event of a covered loss.",
            "Repair or Replacement \u2014 Applies to repair or replacement costs of the vehicle.",
            "New Vehicles \u2014 Generally available for new vehicles or vehicles only a few years old.",
        ]
    },
]

# =============================================================================
# VERTEX AI EXTRACTION
# =============================================================================
EXTRACTION_PROMPT = """
You are an insurance data extraction assistant. You will receive an Ontario auto insurance quote PDF.

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
    "effectiveDate": "MM/DD/YYYY"
  },
  "vehicles": [
    {
      "id": 1,
      "description": "YEAR MAKE MODEL TRIM BODYSTYLE DRIVETRAIN (e.g. 2025 NISSAN ROGUE S 4DR AWD). Do NOT include internal codes/numbers in parentheses.",
      "discounts": [
        "Clean discount name only (e.g. Multi Line, Multi Vehicle, Private Parking, Winter Tire, Mature Driver, Minor Conviction Free, Claims Free). Remove percentage values, remove suffixes like '= 10% - Prn', keep ONLY the human-readable discount name."
      ],
      "coverages": [
        {
          "name": "Coverage name (e.g. Bodily Injury, Property Damage, Direct Compensation, Accident Benefits, Comprehensive, Collision, All Perils, Uninsured Automobile, Loss of Use (OPCF 20), Family Protection (OPCF 44), Mortgage (OPCF 23a), Limited Waiver of Depreciation (OPCF 43), Accident Waiver, etc.)",
          "limit": "Formatted dollar limit or deductible — see formatting rules below"
        }
      ]
    }
  ]
}

IMPORTANT RULES:

COVERAGE LIMIT FORMATTING — YOU MUST follow these rules exactly:
- Always use full dollar amounts with dollar sign and commas: "$2,000,000" NOT "2 M" or "2,000,000"
- For deductibles, append the word Deductible: "$1,000 Deductible", "$500 Deductible", "$0 Deductible"
- For "Included" coverages (Accident Benefits, Uninsured Automobile, etc.): use the exact word "Included"
- For million-dollar limits like "2 M" or "2M": convert to "$2,000,000"
- For thousand-dollar amounts like "1,000" or "1000": add dollar sign "$1,000"
- For deductible amounts: always append " Deductible" (e.g. "$500 Deductible", "$1,000 Deductible")
- For non-dollar limits like time periods: write as-is (e.g. "24 Months")
- For Loss of Use limits: format as "$1,500" (dollar amount only, no "Deductible" suffix)
- Bodily Injury and Property Damage are liability limits, NOT deductibles (e.g. "$2,000,000")
- Direct Compensation limit is a deductible (e.g. "$0 Deductible", "$500 Deductible")
- Comprehensive, Collision, All Perils are deductibles (e.g. "$500 Deductible", "$1,000 Deductible")

DISCOUNT FORMATTING:
- Extract ONLY the discount NAME, not the percentage or code
- Remove any "= XX%" or "- Prn" or "- Sec" suffixes
- Examples: "Multi Line", "Multi Vehicle", "Private Parking", "Winter Tire", "Mature Driver"

VEHICLE DESCRIPTION:
- Include Year Make Model Trim Bodystyle Drivetrain in ALL CAPS
- Remove any internal reference numbers in parentheses like (699301)

OTHER RULES:
- If a field is missing or not found, use an empty string "" or empty array []
- Do NOT include any premiums, costs, taxes, or payment details anywhere in the output
- Extract ALL vehicles in the quote, not just the first one
- Return ONLY the JSON — no markdown, no explanation, no code fences
"""


def _normalise_vehicle(v: dict, idx: int) -> dict:
    """Ensure a vehicle dict from AI extraction has all required keys with safe defaults."""
    return {
        "id":          v.get("id", idx + 1),
        "description": v.get("description", f"Vehicle {idx + 1}"),
        "discounts":   [str(d) for d in v.get("discounts", []) if d],
        "coverages":   [
            {"name": str(c.get("name", "")), "limit": str(c.get("limit", ""))}
            for c in v.get("coverages", [])
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
        # Normalise vehicles so downstream code never gets a KeyError
        data["vehicles"] = [
            _normalise_vehicle(v, i) for i, v in enumerate(data.get("vehicles", []))
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
# PDF GENERATION - ReportLab letter layout
# =============================================================================
def generate_pdf(client_info, broker_info, policy_info, vehicles):
    buf = BytesIO()

    # ── Colour palette ────────────────────────────────────────────────────────
    C_INK      = colors.HexColor("#0f172a")   # deep navy – primary text
    C_DARK     = colors.HexColor("#1e293b")   # dark slate
    C_MID      = colors.HexColor("#334155")   # mid slate
    C_MUTED    = colors.HexColor("#64748b")   # muted label text
    C_LIGHT    = colors.HexColor("#94a3b8")   # light helper text
    C_BORDER   = colors.HexColor("#e2e8f0")   # light border
    C_SURFACE  = colors.HexColor("#f8fafc")   # off-white panel bg
    C_GREEN_BG = colors.HexColor("#ecfdf5")   # "Included" cell fill
    C_GREEN_FG = colors.HexColor("#047857")   # "Included" text
    C_GREEN_BD = colors.HexColor("#a7f3d0")   # "Included" border
    C_BLUE     = colors.HexColor("#1d4ed8")   # status blue
    C_COV_HDR  = colors.HexColor("#1e293b")   # coverage table header bg
    C_ACCENT   = colors.HexColor("#1d4ed8")   # accent line colour

    PAGE_W, PAGE_H = letter          # 612 x 792 pt
    MARGIN_X   = 0.65 * inch
    MARGIN_TOP = 0.60 * inch
    MARGIN_BOT = 0.60 * inch
    BODY_W     = PAGE_W - 2 * MARGIN_X           # ≈ 500.4 pt

    # ── Style factory ─────────────────────────────────────────────────────────
    def S(name, **kw):
        defaults = dict(fontName="Helvetica", fontSize=9, leading=13,
                        textColor=C_INK, spaceAfter=0, spaceBefore=0)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    # Document-level styles
    sNormal   = S("normal")
    sSub      = S("sub",   fontName="Helvetica-Bold", fontSize=7,    textColor=C_MUTED,  leading=10,
                            letterSpacing=1.2)
    sTitle    = S("title", fontName="Helvetica",      fontSize=22,   textColor=C_INK,    leading=26)
    sPrepLbl  = S("plbl",  fontName="Helvetica-Bold", fontSize=6.5,  textColor=C_LIGHT,  leading=9,
                            alignment=TA_RIGHT)
    sPrepName = S("pname", fontName="Helvetica-Bold", fontSize=12,   textColor=C_DARK,   leading=15,
                            alignment=TA_RIGHT)

    # Ribbon styles
    sRibLbl   = S("rl",  fontName="Helvetica-Bold", fontSize=6.5, textColor=C_MUTED,  leading=9)
    sRibVal   = S("rv",  fontName="Helvetica-Bold", fontSize=10,  textColor=C_INK,    leading=13)
    sRibValB  = S("rvb", fontName="Helvetica-Bold", fontSize=10,  textColor=C_BLUE,   leading=13)

    # Panel styles
    sPanelHead= S("ph",  fontName="Helvetica-Bold", fontSize=9,   textColor=C_INK)
    sPanelAddr= S("pa",  fontSize=8.5,              textColor=C_MID,  leading=12)
    sPanelCont= S("pc",  fontSize=8.5,              textColor=C_INK,  leading=12)

    # Section header styles
    sSecLabel = S("sl",  fontName="Helvetica-Bold", fontSize=7,   textColor=C_MUTED,  leading=10)
    sFooter   = S("ft",  fontSize=7.5,              textColor=C_MUTED, leading=11)

    # Discount inline style (used once per vehicle, defined here to avoid re-creation)
    sDiscInline = S("di", fontSize=8.5, textColor=C_MID, leading=13, fontName="Helvetica")

    # Vehicle header styles (used on white bg for veh #)
    sVehNum   = S("vn",  fontName="Helvetica-Bold", fontSize=7,   textColor=colors.white, leading=10)
    sVehDesc  = S("vd",  fontName="Helvetica-Bold", fontSize=12,  textColor=colors.white, leading=15)

    # Coverage table styles
    sCovHdrL  = S("chl", fontName="Helvetica-Bold", fontSize=7,   textColor=colors.white, leading=10)
    sCovHdrR  = S("chr", fontName="Helvetica-Bold", fontSize=7,   textColor=colors.white, leading=10,
                          alignment=TA_RIGHT)
    sCovName  = S("cn",  fontName="Helvetica-Bold", fontSize=9,   textColor=C_DARK,   leading=12)
    sCovLimit = S("cl",  fontName="Helvetica-Bold", fontSize=8.5, textColor=C_INK,    leading=11,
                          alignment=TA_RIGHT)
    sCovInc   = S("ci",  fontName="Helvetica-Bold", fontSize=8.5, textColor=C_GREEN_FG, leading=11,
                          alignment=TA_RIGHT)

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
        """Thin labelled section separator — label on left, line below."""
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
        """Signature + legal notice footer row."""
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

    # ═══════════════════════════════════════════════════════════════════════════
    # 1.  HEADER
    # ═══════════════════════════════════════════════════════════════════════════
    hdr = Table([[
        [Paragraph("AUTOMOBILE INSURANCE", sSub),
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
    # Accent rule below header
    story.append(HRFlowable(width=BODY_W, thickness=2, color=C_ACCENT, spaceAfter=0))

    # ═══════════════════════════════════════════════════════════════════════════
    # 2.  RIBBON  (4 metrics with vertical dividers)
    # ═══════════════════════════════════════════════════════════════════════════
    col_w = BODY_W / 4
    ribbon = Table([[
        [Paragraph("PROPOSED EFF. DATE", sRibLbl),
         Paragraph(policy_info["effectiveDate"], sRibVal)],
        [Paragraph("INSURANCE PROVIDER", sRibLbl),
         Paragraph(policy_info["company"], sRibVal)],
        [Paragraph("VEHICLES", sRibLbl),
         Paragraph(f"{len(vehicles)} Vehicle{'s' if len(vehicles) != 1 else ''} on Policy", sRibVal)],
        [Paragraph("STATUS", sRibLbl),
         Paragraph("Pending Binding", sRibValB)],
    ]], colWidths=[col_w] * 4, rowHeights=[40])
    ribbon.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), C_SURFACE),
        ("LINEABOVE",    (0,0),(-1, 0), 1,   C_ACCENT),
        ("LINEBELOW",    (0,0),(-1,-1), 0.5, C_BORDER),
        # Vertical dividers between cells
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
    # 4.  VEHICLES
    #     – Vehicle 1: continues after panels on page 1
    #     – Vehicles 2+: each starts on a fresh page
    # ═══════════════════════════════════════════════════════════════════════════
    for idx, vehicle in enumerate(vehicles):
        if idx == 0:
            story.append(Spacer(1, 18))
        else:
            story.append(PageBreak())

        # ── Vehicle header band (dark navy bar) ───────────────────────────────
        vhdr = Table([[
            Paragraph(f"VEHICLE {vehicle['id']}", sVehNum),
            Paragraph(vehicle["description"], sVehDesc),
        ]], colWidths=[56, BODY_W - 56], rowHeights=[36])
        vhdr.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,-1), C_INK),
            ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
            ("LEFTPADDING",  (0,0),(-1,-1), 14),
            ("RIGHTPADDING", (0,0),(-1,-1), 14),
            ("TOPPADDING",   (0,0),(-1,-1), 0),
            ("BOTTOMPADDING",(0,0),(-1,-1), 0),
            # Thin accent left border
            ("LINEABOVE",    (0,0),(-1, 0), 2, C_ACCENT),
        ]))
        story.append(vhdr)
        story.append(Spacer(1, 10))

        # ── Applied Discounts — all inline, horizontal ─────────────────────────
        story.append(section_rule("Applied Discounts"))
        story.append(Spacer(1, 5))
        disc_html = "   ".join(
            f'<font color="#1d4ed8"><b>\u2713</b></font> {d}'
            for d in vehicle["discounts"]
        )
        story.append(Paragraph(disc_html, sDiscInline))
        story.append(Spacer(1, 8))

        # ── Schedule of Coverages ─────────────────────────────────────────────
        story.append(section_rule(f"Schedule of Coverages — Vehicle {vehicle['id']}"))
        story.append(Spacer(1, 5))

        cov_col1 = BODY_W * 0.65
        cov_col2 = BODY_W * 0.35
        cov_data = [[
            Paragraph("COVERAGE DESCRIPTION", sCovHdrL),
            Paragraph("LIMIT / DEDUCTIBLE",   sCovHdrR),
        ]]
        for cov in vehicle["coverages"]:
            lp = Paragraph(cov["limit"], sCovInc if cov["limit"] == "Included" else sCovLimit)
            cov_data.append([Paragraph(cov["name"], sCovName), lp])

        cov_tbl = Table(cov_data, colWidths=[cov_col1, cov_col2])
        cov_styles = [
            # Header row — dark navy background
            ("BACKGROUND",    (0,0),(-1, 0),  C_COV_HDR),
            ("BOX",           (0,0),(-1,-1),  0.75, C_BORDER),
            ("LINEBELOW",     (0,0),(-1, 0),  1,    C_ACCENT),
            # Header padding — spacious
            ("TOPPADDING",    (0,0),(-1, 0),  6),
            ("BOTTOMPADDING", (0,0),(-1, 0),  6),
            ("LEFTPADDING",   (0,0),(-1,-1),  12),
            ("RIGHTPADDING",  (0,0),(-1,-1),  12),
            ("VALIGN",        (0,0),(-1,-1),  "MIDDLE"),
        ]
        # Data rows: alternating stripe + divider lines
        for i in range(1, len(cov_data)):
            bg = C_SURFACE if i % 2 == 0 else colors.white
            cov_styles += [
                ("BACKGROUND",    (0,i),(-1,i),  bg),
                ("TOPPADDING",    (0,i),(-1,i),  5),
                ("BOTTOMPADDING", (0,i),(-1,i),  4),
                ("LINEBELOW",     (0,i),(-1,i),  0.5, C_BORDER),
            ]
        # "Included" cells — green tint on value column only
        for i, cov in enumerate(vehicle["coverages"], start=1):
            if cov["limit"] == "Included":
                cov_styles += [
                    ("BACKGROUND",  (1,i),(1,i), C_GREEN_BG),
                    ("LINEAFTER",   (1,i),(1,i), 0.75, C_GREEN_BD),
                    ("LINEBEFORE",  (1,i),(1,i), 0.5,  C_BORDER),
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

    # Endorsements page header
    endo_title_style = S("et", fontName="Helvetica-Bold", fontSize=13, textColor=C_INK, leading=16, spaceAfter=2)
    endo_subtitle = S("es", fontName="Helvetica", fontSize=7.5, textColor=C_MUTED, leading=10, spaceAfter=12)
    endo_heading  = S("eh", fontName="Helvetica-Bold", fontSize=9, textColor=C_INK, leading=12, spaceBefore=10, spaceAfter=3)
    endo_bullet   = S("eb", fontName="Helvetica", fontSize=7.5, textColor=C_MID, leading=11,
                       leftIndent=12, bulletIndent=0, bulletFontName="Helvetica", bulletFontSize=7.5,
                       bulletColor=C_MUTED)

    story.append(Paragraph("Endorsement Descriptions", endo_title_style))
    story.append(Paragraph("The following endorsements may apply to your policy. Please refer to your policy documents for full terms and conditions.", endo_subtitle))
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

# ── Upload & Extract ─────────────────────────────────────────────────────────
st.markdown("### Upload Quote PDF")
with st.expander("📄 Upload an auto insurance quote to auto-populate the coverpage", expanded=True):
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
                        st.session_state["extracted"] = result
                        st.success("Extraction complete — coverpage updated below.")
                        st.rerun()

st.divider()

# Use extracted data if available, otherwise fall back to defaults
_data = st.session_state.get("extracted", {})
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
        "effectiveDate": _pi.get("effectiveDate", policy_info["effectiveDate"]),
    }
    _veh = _data.get("vehicles") or []
    if _veh:
        vehicles = _veh

if REPORTLAB_AVAILABLE:
    pdf_buf = generate_pdf(client_info, broker_info, policy_info, vehicles)
    fname = f"Auto_CoverPage_{client_info['name'].replace(' ', '_') or 'Coverage'}.pdf"
    st.download_button(
        label="Export as PDF",
        data=pdf_buf,
        file_name=fname,
        mime="application/pdf"
    )
else:
    st.warning("Install reportlab to enable PDF export: pip install reportlab")

# Escape all user-supplied values before inserting into HTML
_e = _html.escape  # shorthand
_veh_label = f"{len(vehicles)} Vehicle{'s' if len(vehicles) != 1 else ''} on Policy"

# Header
st.markdown(f"""
<div style="display: flex; justify-content: space-between; align-items: flex-end; padding-bottom: 20px;">
    <div>
        <div style="color: #64748b; letter-spacing: 0.2em; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; margin-bottom: 5px;">Automobile Insurance</div>
        <div style="font-size: 2.5rem; font-weight: 300; line-height: 1; color: #0f172a; letter-spacing: -0.02em;">Coverage Summary</div>
    </div>
    <div style="text-align: right; border-right: 2px solid #e2e8f0; padding-right: 15px;">
        <div style="color: #94a3b8; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 2px;">Prepared For</div>
        <div style="font-size: 1.25rem; font-weight: 500; color: #1e293b;">{_e(client_info['name'])}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# Ribbon
st.markdown(f"""
<div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; padding: 15px 20px; display: flex; justify-content: space-between; margin-bottom: 30px;">
    <div><div class="metric-label">Proposed Eff. Date</div><div class="metric-val">{_e(policy_info['effectiveDate'])}</div></div>
    <div><div class="metric-label">Insurance Provider</div><div class="metric-val">{_e(policy_info['company'])}</div></div>
    <div><div class="metric-label">Vehicles</div><div class="metric-val">{_veh_label}</div></div>
    <div><div class="metric-label">Status</div><div class="metric-val" style="color: #1d4ed8;">Pending Binding</div></div>
</div>
""", unsafe_allow_html=True)

# Panels
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

# Vehicles
for vehicle in vehicles:
    st.markdown(f"""
    <div style="border-top: 2px solid #0f172a; padding-top: 30px; margin-top: 20px; margin-bottom: 20px;">
        <h2 style="font-size: 1.5rem; font-weight: 300; color: #0f172a; margin: 0;">
            <span style="font-weight: 700; color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.15em; margin-right: 10px;">Veh {_e(str(vehicle['id']))}</span>
            {_e(vehicle['description'])}
        </h2>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-title" style="margin-top:0;">Applied Discounts</div>', unsafe_allow_html=True)
    disc_inline = "   ".join(
        f"<span style='color:#1d4ed8; font-weight:700;'>&#10003;</span>"
        f"<span style='color:#334155; font-size:0.875rem; margin-left:5px;'>{_e(d)}</span>"
        for d in vehicle['discounts']
    )
    st.markdown(f"<div style='margin-bottom:12px; font-size:0.875rem; line-height:1.8;'>{disc_inline}</div>", unsafe_allow_html=True)
    st.markdown(f'<div class="section-title">Schedule of Coverages - Veh {vehicle["id"]}</div>', unsafe_allow_html=True)

    table_html = """
    <div style="border: 1px solid #e2e8f0; border-radius: 4px; overflow: hidden; margin-bottom: 40px;">
        <table style="width:100%; text-align:left; border-collapse: collapse; font-family: sans-serif; font-size: 0.9rem;">
            <thead style="background-color: #f8fafc; border-bottom: 1px solid #e2e8f0;">
                <tr>
                    <th style="padding: 12px 20px; font-weight: 600; color: #334155; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; width: 66%;">Coverage Description</th>
                    <th style="padding: 12px 20px; font-weight: 600; color: #334155; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; text-align: right; width: 33%;">Limit / Deductible</th>
                </tr>
            </thead>
            <tbody>
    """
    for cov in vehicle['coverages']:
        if cov['limit'] == "Included":
            ls = "background-color: #ecfdf5; color: #047857; padding: 4px 10px; border-radius: 4px; border: 1px solid #d1fae5;"
        else:
            ls = "color: #0f172a;"
        table_html += f"""
                <tr style="border-bottom: 1px solid #f1f5f9; background-color: #ffffff;">
                    <td style="padding: 12px 20px; color: #1e293b; font-weight: 500;">{_e(cov['name'])}</td>
                    <td style="padding: 12px 20px; text-align: right;">
                        <span style="{ls} font-weight: 600; font-size: 0.85rem;">{_e(cov['limit'])}</span>
                    </td>
                </tr>"""
    table_html += "</tbody></table></div>"
    components.html(table_html, height=len(vehicle['coverages']) * 50 + 80, scrolling=False)

# Endorsement Descriptions
st.markdown("""<div style="border-top: 2px solid #0f172a; padding-top: 24px; margin-top: 40px;">
    <h3 style="font-size: 1.05rem; font-weight: 600; color: #0f172a; margin: 0 0 4px 0;">Endorsement Descriptions</h3>
    <p style="font-size: 0.72rem; color: #94a3b8; margin: 0 0 12px 0;">The following endorsements may apply to your policy. Please refer to your policy documents for full terms and conditions.</p>
</div>""", unsafe_allow_html=True)

for endo in ENDORSEMENTS:
    st.markdown(f"""
    <div style="margin-bottom: 14px;">
        <div style="font-size: 0.82rem; font-weight: 700; color: #0f172a; margin-bottom: 5px;">{endo['title']}</div>
        {''.join(f'<div style="font-size: 0.75rem; color: #475569; line-height: 1.6; padding-left: 12px;">\u2022&ensp;{b}</div>' for b in endo['bullets'])}
    </div>
    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 0;">
    """, unsafe_allow_html=True)

# Footer
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
