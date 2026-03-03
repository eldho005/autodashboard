"""
Meta/Facebook Lead Form Backend
Integrates with Facebook Lead API and Supabase
"""

import sys
import io
import os
from io import BytesIO

# Add backend directory to sys.path so local modules (pdf_parser, quote_extraction_schema_v2, etc.)
# are importable both when run directly and when run as a package via gunicorn
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure UTF-8 encoding for Windows console
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import json
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room
from dotenv import load_dotenv
from supabase import create_client, Client
import hmac
import hashlib
import jwt
import bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
# Import pdf_parser
import pdf_parser
parse_mvr_pdf = pdf_parser.parse_mvr_pdf
parse_dash_pdf = pdf_parser.parse_dash_pdf
parse_quote_pdf = pdf_parser.parse_quote_pdf
parse_property_quote_pdf = pdf_parser.parse_property_quote_pdf

# Load environment variables from backend/.env and parent .env.local
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '../.env.local'))

# Configure Flask to serve static files from parent directory
STATIC_FOLDER = os.path.join(os.path.dirname(__file__), '..')
app = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path='')

# ========== CORS CONFIGURATION ==========
# Reads comma-separated origins from ALLOWED_ORIGINS env var.
# Local default: localhost:5000. Production: add Railway URL in Railway env vars.
_raw_origins = os.getenv('ALLOWED_ORIGINS', 'http://localhost:5000,http://127.0.0.1:5000')
ALLOWED_ORIGINS = [o.strip().rstrip('/') for o in _raw_origins.split(',') if o.strip()]
CORS(app,
     origins=ALLOWED_ORIGINS,
     supports_credentials=True,
     allow_headers=['Content-Type', 'Authorization'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS']
)
print(f"🔒 CORS allowed origins: {ALLOWED_ORIGINS}")

# SocketIO async mode: use 'eventlet' only when already monkeypatched by the
# Gunicorn eventlet worker. Fall back to 'threading' for direct `python app.py`
# usage.
# IMPORTANT: Do NOT import eventlet.patcher unconditionally — the import alone
# has side-effects that corrupt httpx / h2, breaking the Supabase client.
# Only touch it if Gunicorn's eventlet worker has already loaded it.
if 'eventlet.patcher' in sys.modules:
    _async_mode = 'eventlet' if sys.modules['eventlet.patcher'].is_monkey_patched('threading') else 'threading'
else:
    _async_mode = 'threading'
print(f"⚙️  SocketIO async_mode: {_async_mode}")

socketio = SocketIO(app, cors_allowed_origins=ALLOWED_ORIGINS,
                     async_mode=_async_mode,
                     ping_timeout=60,
                     ping_interval=25,
                     engineio_logger=False)

# ========== RATE LIMITING ==========
# Uses in-memory storage (safe with single Gunicorn worker).
# On Railway, set RATELIMIT_STORAGE_URI=redis://... to persist limits across restarts.
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=['200 per minute'],
    storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
)

# ========== CONFIG ==========
# Set COOKIE_SECURE=true in Railway env vars (HTTPS). Leave unset for local HTTP dev.
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
# Set ENABLE_DEBUG_ENDPOINTS=true ONLY during local debugging. Never in production.
ENABLE_DEBUG_ENDPOINTS = os.getenv('ENABLE_DEBUG_ENDPOINTS', 'false').lower() == 'true'

META_APP_ID = os.getenv('META_APP_ID')
META_APP_SECRET = os.getenv('META_APP_SECRET')
META_PAGE_ID = os.getenv('META_PAGE_ID')
META_PAGE_ACCESS_TOKEN = os.getenv('META_PAGE_ACCESS_TOKEN')
META_LEAD_FORM_ID = os.getenv('META_LEAD_FORM_ID')
META_WEBHOOK_VERIFY_TOKEN = os.getenv('META_WEBHOOK_VERIFY_TOKEN')
FB_PIXEL_ID = os.getenv('FB_PIXEL_ID')
SIGNWELL_WEBHOOK_SECRET = os.getenv('SIGNWELL_WEBHOOK_SECRET')

# Streamlit sidecar URLs — override in Railway env vars when deployed.
# Locally they run on fixed ports; on Railway deploy them as separate services.
STREAMLIT_AUTO_URL = os.getenv('STREAMLIT_AUTO_URL', 'http://localhost:8502')
STREAMLIT_TENANT_URL = os.getenv('STREAMLIT_TENANT_URL', 'http://localhost:8503')
print(f"🎯 Streamlit Auto URL: {STREAMLIT_AUTO_URL}")
print(f"🎯 Streamlit Tenant URL: {STREAMLIT_TENANT_URL}")

SUPABASE_URL = os.getenv('VITE_SUPABASE_URL')
SUPABASE_KEY = os.getenv('VITE_SUPABASE_SERVICE_ROLE_KEY')

# Debug: Print Supabase URL (key hidden for security)
print(f"🔗 Supabase URL: {SUPABASE_URL}")
print(f"🔑 Supabase Key: {'configured (' + str(len(SUPABASE_KEY)) + ' chars)' if SUPABASE_KEY else 'MISSING'}")

# Initialize Supabase
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized successfully")
except Exception as e:
    print(f"❌ Error initializing Supabase client: {e}")
    print("⚠️ Please verify your VITE_SUPABASE_SERVICE_ROLE_KEY in backend/.env")
    print("   Get it from: Supabase Dashboard > Settings > API > service_role key")
    raise

# Meta API Base URL
META_API_VERSION = 'v18.0'
META_BASE_URL = f'https://graph.facebook.com/{META_API_VERSION}'

# ========== FILE UPLOAD LIMITS ==========
# Default 32MB — enough for multi-page PDFs. Override via MAX_UPLOAD_MB env var.
_max_mb = int(os.getenv('MAX_UPLOAD_MB', '32'))
app.config['MAX_CONTENT_LENGTH'] = _max_mb * 1024 * 1024
print(f"📦 Max upload size: {_max_mb}MB")

@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({'error': f'File too large. Maximum allowed size is {_max_mb}MB.'}), 413

# ========== JWT AUTHENTICATION CONFIG ==========
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')
JWT_EXPIRY_HOURS = 8
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
ADMIN_PASSWORD_HASH = os.getenv('ADMIN_PASSWORD_HASH')

# Endpoints that do NOT require authentication
PUBLIC_ENDPOINTS = {
    '/api/login',
    '/api/logout',
    '/api/verify-token',
    '/api/health',
    '/api/streamlit-config',
    '/webhook',
    '/api/signwell/webhook',
    '/api/signwell/webhook-legacy',
}

# Prefixes for static file serving (HTML pages handle their own auth guard)
PUBLIC_PREFIXES = (
    '/static/',
)

def _is_public_route(path):
    """Check if a request path is public (no JWT required)"""
    if path in PUBLIC_ENDPOINTS:
        return True
    if path.endswith(('.html', '.css', '.js', '.ico', '.png', '.jpg', '.svg', '.woff', '.woff2', '.ttf')):
        return True
    for prefix in PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False

@app.before_request
def authenticate_request():
    """Global JWT authentication middleware.
    Runs before every request. Public endpoints and static files are skipped.
    All API endpoints require a valid JWT token in the 'auth_token' cookie.
    """
    if request.method == 'OPTIONS':
        return  # Allow CORS preflight through

    if _is_public_route(request.path):
        return  # Public route, no auth needed

    # Also allow the root page and named page routes through (they serve HTML)
    if request.path in ('/', '/auto', '/signwell-ui', '/document-upload-dashboard', '/login'):
        return

    token = request.cookies.get('auth_token')
    if not token:
        return jsonify({'error': 'Authentication required', 'code': 'NO_TOKEN'}), 401

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
        # Attach user info to request context for downstream use
        request.auth_user = payload.get('email')
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token expired', 'code': 'TOKEN_EXPIRED'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token', 'code': 'INVALID_TOKEN'}), 401

# ========== HELPER FUNCTIONS ==========

def verify_meta_webhook(data, hub_signature):
    """Verify webhook signature from Meta"""
    hash_obj = hmac.new(
        META_APP_SECRET.encode('utf-8'),
        data,
        hashlib.sha256
    )
    expected_signature = f'sha256={hash_obj.hexdigest()}'
    return hmac.compare_digest(expected_signature, hub_signature)


def _verify_signwell_webhook(req):
    """Verify incoming SignWell webhook against SIGNWELL_WEBHOOK_SECRET.

    Accepts the token via:
      1. X-SignWell-Webhook-Token header
      2. ?token= query parameter (for callback URLs registered with the token appended)

    If SIGNWELL_WEBHOOK_SECRET is not set, verification is SKIPPED (open) —
    set it in .env and add ?token=<value> to your SignWell callback URL.
    """
    if not SIGNWELL_WEBHOOK_SECRET:
        print('⚠️  SIGNWELL_WEBHOOK_SECRET not set — skipping webhook auth')
        return True
    provided = (
        req.headers.get('X-SignWell-Webhook-Token')
        or req.args.get('token')
    )
    if not provided:
        return False
    return hmac.compare_digest(provided, SIGNWELL_WEBHOOK_SECRET)








def get_leads_from_meta():
    """Fetch all leads from Meta Lead Form API with pagination (up to 500 leads)"""
    try:
        all_leads = []
        url = f'{META_BASE_URL}/{META_LEAD_FORM_ID}/leads'
        
        params = {
            'fields': 'id,created_time,field_data,ad_id,form_id,adset_id,campaign_id',
            'access_token': META_PAGE_ACCESS_TOKEN,
            'limit': 500  # Request max leads per page
        }
        
        print(f"📞 Fetching leads from Meta API: {url}")
        print(f"🔑 Using Lead Form ID: {META_LEAD_FORM_ID}")
        
        # Fetch first page
        response = requests.get(url, params=params, timeout=30)
        print(f"📡 Meta API Response Status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ Meta API Error: {response.text}")
            response.raise_for_status()
        
        data = response.json()
        all_leads.extend(data.get('data', []))
        print(f"📄 Page 1: {len(data.get('data', []))} leads")
        
        # Continue fetching all pages (max 500 total to avoid overwhelming)
        page_count = 1
        while 'paging' in data and 'next' in data['paging'] and len(all_leads) < 500:
            page_count += 1
            next_url = data['paging']['next']
            print(f"📄 Fetching page {page_count}... (Total so far: {len(all_leads)})")
            response = requests.get(next_url, timeout=30)
            response.raise_for_status()
            data = response.json()
            page_leads = data.get('data', [])
            all_leads.extend(page_leads)
            print(f"   + {len(page_leads)} leads")
            
            # Safety limit
            if page_count > 10:
                print(f"⚠️ Reached page limit (10 pages)")
                break
        
        print(f"✅ Found {len(all_leads)} total leads from Meta")
        return all_leads
    
    except Exception as e:
        print(f"❌ Error fetching leads from Meta: {str(e)}")
        if hasattr(e, 'response') and e.response:
            print(f"❌ Meta API Error Response: {e.response.text}")
        return []


def check_auto_qualified(driver_license_answer):
    """Check if driver is qualified based on license date (2020 or before = 5+ years experience)"""
    if not driver_license_answer:
        return False
    
    # Extract year from various formats
    import re
    
    # Try to find a 4-digit year
    year_match = re.search(r'\b(19|20)\d{2}\b', str(driver_license_answer))
    if year_match:
        year = int(year_match.group(0))
        # Qualified if 2020 or before (5+ years experience)
        return year <= 2020
    
    # If no year found, return False (not qualified)
    return False


def parse_meta_lead(meta_lead):
    """Parse Meta lead data into standardized format"""
    field_data = meta_lead.get('field_data', [])
    lead_dict = {}
    
    # Facebook returns field_data with "values" as an array
    for field in field_data:
        field_name = field.get('name', '').lower()
        field_values = field.get('values', [])
        # Take the first value from the array
        lead_dict[field_name] = field_values[0] if field_values else ''
    
    # Debug: Print available fields
    print(f"📋 Available fields in Facebook lead: {list(lead_dict.keys())}")
    
    meta_lead_id = meta_lead.get('id')

    def normalize_key(value):
        if not value:
            return ''
        cleaned = ''.join(ch if ch.isalnum() or ch.isspace() else ' ' for ch in str(value).lower())
        cleaned = ' '.join(cleaned.replace('_', ' ').split())
        return cleaned

    def normalize_value(value):
        if not isinstance(value, str):
            return value
        if '_' in value and ' ' not in value:
            return value.replace('_', ' ')
        return value

    normalized_lookup = {}
    for key, value in lead_dict.items():
        normalized_lookup[normalize_key(key)] = value

    # Extract name from various possible field names
    def extract_name():
        """Try to extract name from various possible field names"""
        # Try exact matches first
        name_fields = ['full_name', 'fullname', 'name', 'full name', 
                       'your name', 'your full name', 'customer name',
                       'first_name', 'firstname', 'first name',
                       'last_name', 'lastname', 'last name']
        
        for field_name in name_fields:
            if field_name in lead_dict:
                return lead_dict[field_name]
        
        # Try normalized matches
        for normalized_key, value in normalized_lookup.items():
            if 'full name' in normalized_key or normalized_key == 'name':
                return value
            if 'your name' in normalized_key:
                return value
        
        # Try to combine first and last name if available
        first_name = lead_dict.get('first_name', lead_dict.get('firstname', ''))
        last_name = lead_dict.get('last_name', lead_dict.get('lastname', ''))
        if first_name or last_name:
            return f"{first_name} {last_name}".strip()
        
        # If nothing found, check all fields for anything containing 'name'
        for key, value in lead_dict.items():
            if 'name' in key.lower() and value:
                return value
        
        return 'Unknown'
    
    extracted_name = extract_name()

    target_keys = [
        "when did you first receive your g or g2 driver's licence",
        "when did you first receive your g or g2 drivers licence",
        "when did you first receive your g or g2 driver's license",
        "when did you first receive your g or g2 drivers license",
        "driver license received",
        "driver licence received",
        "g or g2 driver license",
        "g or g2 driver licence",
        "driver_license_received",
    ]

    driver_license_answer = ''
    for key in target_keys:
        normalized_key = normalize_key(key)
        if normalized_key in normalized_lookup:
            driver_license_answer = normalized_lookup.get(normalized_key, '')
            break

    if not driver_license_answer:
        for normalized_key, value in normalized_lookup.items():
            if 'g or g2' in normalized_key and 'driver' in normalized_key and ('licence' in normalized_key or 'license' in normalized_key):
                driver_license_answer = value
                break

    driver_license_answer = normalize_value(driver_license_answer)
    
    # Check if auto qualified (5+ years experience = 2020 or before)
    is_auto_qualified = check_auto_qualified(driver_license_answer)
    
    # Extract ad attribution IDs for CAPI optimization
    ad_id = meta_lead.get('ad_id', '')
    form_id = meta_lead.get('form_id', '')
    adset_id = meta_lead.get('adset_id', '')
    campaign_id = meta_lead.get('campaign_id', '')
    
    return {
        # Don't set 'id' - let database auto-generate it
        'meta_lead_id': meta_lead_id,
        'name': extracted_name,
        'phone': lead_dict.get('phone_number', lead_dict.get('phone', lead_dict.get('phone number', ''))),
        'email': lead_dict.get('email_address', lead_dict.get('email', lead_dict.get('email address', ''))),
        'message': lead_dict.get('message', ''),
        'created_at': meta_lead.get('created_time', datetime.now(timezone.utc).isoformat()),
        'meta_data': meta_lead,
        'is_manual': False,
        'status': 'New Lead',
        'type': 'Auto',
        'potential_status': 'Not Qualified',
        'premium': 0,
        'sync_status': 'Not Synced',
        'sync_signal': 'pending',
        'notes': '',
        'driver_license_received': driver_license_answer,
        'is_auto_qualified': is_auto_qualified,
        # Attribution IDs for CAPI optimization
        'ad_id': ad_id,
        'form_id': form_id,
        'adset_id': adset_id,
        'campaign_id': campaign_id
        # Removed: company, address, city, state, country, zip_code - not in database schema
    }


def save_lead_to_supabase(lead_data):
    """Save lead to Supabase (skip if already exists)"""
    try:
        # Check if lead already exists by meta_lead_id (only for Facebook leads)
        if lead_data.get('meta_lead_id'):
            existing = supabase.table('leads').select('id').eq('meta_lead_id', lead_data.get('meta_lead_id')).execute()
            
            if existing.data:
                print(f"⏭️ Lead {lead_data.get('meta_lead_id')} already exists, skipping")
                return existing.data[0]
        
        # Insert new lead
        print(f"🔄 Attempting to save lead: {lead_data.get('name')}")
        response = supabase.table('leads').insert(lead_data).execute()
        print(f"💾 Saved lead: {lead_data.get('name')} (ID: {response.data[0].get('id') if response.data else 'N/A'})")
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"❌ Error saving lead to Supabase: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def get_leads_from_db(filters=None):
    """Get leads from Supabase with personal information from auto_data"""
    try:
        print(f"📋 Querying leads table with filters: {filters}")
        query = supabase.table('leads').select('*')
        
        if filters:
            if filters.get('type'):
                query = query.eq('type', filters['type'])
            if filters.get('status'):
                query = query.eq('status', filters['status'])
        
        # No limit - fetch ALL leads
        response = query.order('created_at', desc=True).execute()
        leads = response.data
        print(f"✅ Query returned {len(leads)} leads")
        
        # Batch-enrich leads with personal information from auto_data.
        # Single IN query replaces the previous N+1 loop (one query per lead).
        emails = list({lead['email'].strip().lower() for lead in leads if lead.get('email')})
        auto_data_by_email = {}
        if emails:
            try:
                auto_result = supabase.table('auto_data').select('email,auto_data').in_('email', emails).execute()
                for row in (auto_result.data or []):
                    email_key = (row.get('email') or '').strip().lower()
                    if email_key and row.get('auto_data'):
                        auto_data_by_email[email_key] = row['auto_data']
                print(f"📊 Batch auto_data lookup: {len(auto_data_by_email)} records for {len(emails)} unique emails")
            except Exception as e:
                print(f"⚠️ Error batch-fetching auto_data: {e}")

        for lead in leads:
            if lead.get('email'):
                email_key = lead['email'].strip().lower()
                auto_data = auto_data_by_email.get(email_key)
                if isinstance(auto_data, dict) and auto_data:
                    # Extract personal information fields
                    lead['first_name'] = auto_data.get('personalFirstName', '')
                    lead['middle_name'] = auto_data.get('personalMiddleName', '')
                    lead['last_name'] = auto_data.get('personalLastName', '')
                    lead['city'] = auto_data.get('personalCity', '')
                    lead['postal_code'] = auto_data.get('personalPostalCode', '')
                    lead['date_of_birth'] = auto_data.get('personalDob', '')
                    lead['address'] = auto_data.get('personalAddress', '')
                    lead['marital_status'] = auto_data.get('personalMaritalStatus', '')
                    lead['gender'] = auto_data.get('personalGender', '')
                    print(f"✅ Enriched lead {lead.get('name')} with personal info")
        
        return leads
    except Exception as e:
        print(f"❌ Error fetching leads from Supabase: {str(e)}")
        import traceback
        traceback.print_exc()
        return []


def send_event_to_meta(lead_id, event_type, event_data):
    """
    Send event to Meta Conversions API (Event Manager)
    
    Implements Meta's standard Conversions API with complete event parameters.
    
    Supported Standard Events (as per Meta's Conversions API documentation):
    - Purchase: Completion of a purchase/transaction
    - Lead: Customer submission expecting follow-up contact
    - Contact: Customer contact via phone/SMS/email/chat
    - Schedule: Booking appointment to visit a location
    - CompleteRegistration: Submission for service (e.g., email subscription)
    - InitiateCheckout: Start of checkout process
    - AddToCart: Item added to shopping basket
    - AddToWishlist: Items added to wishlist
    - ViewContent: Visit to important content page (product, landing, article)
    - Subscribe: Start of paid subscription
    - FindLocation: Finding a location via web/app to visit
    - StartTrial: Start of free trial
    - AddPaymentInfo: Addition of payment info during checkout
    
    Custom Events:
    - QualifiedLead: Lead with 5+ years driving experience (insurance-specific)
    
    Required Parameters (per Meta's documentation):
    - event_time: Unix timestamp (required)
    - event_name: Event type (required)
    - event_id: Unique ID for deduplication (required)
    - action_source: Where event occurred - 'system_generated' for CRM/offline (required)
    - user_data: Customer information parameters (SHA-256 hashed) (required)
    - event_source_url: URL where event occurred (required for website, optional for system_generated)
    - client_user_agent: Browser user agent - do NOT hash (optional, for web events)
    - custom_data: Additional context (value, currency, etc.) (optional)
    
    Args:
        lead_id: Internal lead ID
        event_type: Event name (e.g., 'QualifiedLead', 'Purchase', 'Contact')
        event_data: Dict containing:
            - email: Customer email (hashed)
            - phone: Customer phone (hashed)
            - name: Customer full name (parsed into first/last, hashed)
            - city: Town/city (hashed)
            - zip: Postcode (hashed)
            - country: Country code (hashed, default: 'ca')
            - state: Province/county/region (hashed)
            - date_of_birth: DOB in MM/DD/YYYY or YYYYMMDD format (hashed)
            - premium: Transaction value (optional)
            - meta_lead_id: Facebook Lead ID (optional)
            - external_id: External ID for matching (NOT hashed, defaults to lead_id)
            - event_source_url: Source URL (optional)
            - client_user_agent: User agent string (NOT hashed)
    
    Returns:
        Meta API response dict with events_received, messages, fbtrace_id
    """
    try:
        url = f'{META_BASE_URL}/{FB_PIXEL_ID}/events'
        
        # SHA-256 hash email, phone, and first name for Meta Conversions API
        def hash_for_meta(value):
            """Hash value using SHA-256 for Meta Conversions API"""
            if not value:
                return ''
            # Normalize: lowercase, strip whitespace
            normalized = str(value).lower().strip()
            if not normalized:
                return ''
            # SHA-256 hash
            return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        
        # Extract and hash user data (more fields = better match quality)
        email = event_data.get('email', '')
        phone = event_data.get('phone', '')
        name = event_data.get('name', '')
        
        # Parse first and last name
        name_parts = name.split() if name else []
        first_name = name_parts[0] if len(name_parts) > 0 else ''
        last_name = name_parts[-1] if len(name_parts) > 1 else ''
        
        # Additional fields for better matching
        city = event_data.get('city', '')
        zip_code = event_data.get('zip', '')
        country = event_data.get('country', 'ca')  # Default to Canada for insurance
        state = event_data.get('state', '')  # Province/state/region
        date_of_birth = event_data.get('date_of_birth', '')  # Format: YYYYMMDD or MM/DD/YYYY
        
        # Normalize date of birth to YYYYMMDD format for Meta
        dob_normalized = ''
        if date_of_birth:
            # Handle MM/DD/YYYY format
            if '/' in date_of_birth:
                parts = date_of_birth.split('/')
                if len(parts) == 3:
                    month, day, year = parts[0], parts[1], parts[2]
                    dob_normalized = f"{year}{month.zfill(2)}{day.zfill(2)}"
            # Handle YYYY-MM-DD format
            elif '-' in date_of_birth:
                dob_normalized = date_of_birth.replace('-', '')
            else:
                # Already YYYYMMDD format
                dob_normalized = date_of_birth
        
        meta_lead_id = event_data.get('meta_lead_id', '')
        external_id = event_data.get('external_id', str(lead_id))  # Use lead_id as external_id
        
        # Hash all user data
        em_hash = hash_for_meta(email)
        ph_hash = hash_for_meta(phone)
        fn_hash = hash_for_meta(first_name)
        ln_hash = hash_for_meta(last_name)
        ct_hash = hash_for_meta(city)
        zp_hash = hash_for_meta(zip_code)
        country_hash = hash_for_meta(country)
        st_hash = hash_for_meta(state)  # State/province/region
        db_hash = hash_for_meta(dob_normalized)  # Date of birth (YYYYMMDD)
        
        # Build user_data with all available fields
        user_data = {
            'em': [em_hash] if em_hash else [],  # SHA-256 hashed email
            'ph': [ph_hash] if ph_hash else [],  # SHA-256 hashed phone
            'fn': [fn_hash] if fn_hash else [],  # SHA-256 hashed first name
            'ln': [ln_hash] if ln_hash else [],  # SHA-256 hashed last name
            'ct': [ct_hash] if ct_hash else [],  # SHA-256 hashed city
            'zp': [zp_hash] if zp_hash else [],  # SHA-256 hashed zip/postcode
            'country': [country_hash] if country_hash else [],  # SHA-256 hashed country
            'st': [st_hash] if st_hash else [],  # SHA-256 hashed state/region
            'db': [db_hash] if db_hash else [],  # SHA-256 hashed date of birth
            'external_id': [external_id] if external_id else []  # NOT hashed - lead ID
        }
        
        # Add Facebook lead_id if this is from a lead form
        if meta_lead_id:
            user_data['lead_id'] = meta_lead_id
        
        # Add client_user_agent if available (do not hash - per Meta's guidance)
        client_user_agent = event_data.get('client_user_agent', '')
        if client_user_agent:
            user_data['client_user_agent'] = client_user_agent
        
        # Generate unique event_id for deduplication (Meta uses event_id + event_name for deduplication)
        import time
        # Event ID prefixes for better tracking
        event_prefix_map = {
            'QualifiedLead': 'ql',
            'Purchase': 'sale',
            'Lead': 'lead',
            'Contact': 'contact',
            'Schedule': 'schedule',
            'InitiateCheckout': 'checkout',
            'CompleteRegistration': 'register',
            'ViewContent': 'view',
            'AddToCart': 'cart',
            'Subscribe': 'subscribe'
        }
        event_prefix = event_prefix_map.get(event_type, 'event')
        event_id = f"{event_prefix}-{lead_id}-{int(time.time())}"
        
        # Build custom_data based on event type (with currency and value for all events)
        custom_data = {
            'value': event_data.get('premium', 0),
            'currency': 'CAD',  # Canadian insurance - Meta requires currency with value
            'event_source': 'crm',
            'lead_event_source': 'Insurance Lead Dashboard',
            'business_line': 'auto_insurance'
        }
        
        # Add event-specific context
        if event_type == 'QualifiedLead':
            custom_data['lead_status'] = 'qualified'
            license_year = event_data.get('license_year', '')
            if license_year:
                custom_data['license_years'] = 2026 - int(license_year) if license_year.isdigit() else None
        elif event_type == 'Purchase':
            custom_data['policy_type'] = 'auto_insurance'
            custom_data['lead_status'] = 'sold'
        elif event_type in ['Lead', 'Contact', 'Schedule']:
            # Standard events for lead generation
            custom_data['lead_status'] = 'new'
        
        # Build event data object
        event_obj = {
            'event_name': event_type,  # Standard or custom event name
            'event_time': int(time.time()),  # Current Unix timestamp - Meta requires this
            'event_id': event_id,  # Unique ID for deduplication (event_id + event_name)
            'action_source': 'system_generated',  # CRM/offline source (not website)
            'user_data': user_data,  # Customer information parameters (hashed)
            'custom_data': custom_data  # Additional event context
        }
        
        # Add event_source_url if provided (required for website action_source, optional for system_generated)
        event_source_url = event_data.get('event_source_url', '')
        if event_source_url:
            event_obj['event_source_url'] = event_source_url
        
        payload = {
            'data': [event_obj],
            'access_token': META_PAGE_ACCESS_TOKEN
        }
        
        # Add test_event_code if provided (for testing in Meta Events Manager Test Events tab)
        test_event_code = event_data.get('test_event_code', '')
        if test_event_code:
            payload['test_event_code'] = test_event_code
        
        print(f"📡 Sending to Meta Conversions API...")
        print(f"   URL: {url}")
        print(f"   Event Name: {event_type}")
        print(f"   Event ID: {event_id}")
        print(f"   Test Event Code: {test_event_code if test_event_code else 'N/A (production event)'}")
        print(f"   Event Time: {int(time.time())} (Unix timestamp)")
        print(f"   Action Source: system_generated (CRM/Offline)")
        print(f"   Event Source URL: {event_source_url if event_source_url else 'N/A (offline event)'}")
        print(f"   Facebook Lead ID: {meta_lead_id if meta_lead_id else 'N/A'}")
        print(f"   External ID: {external_id}")
        print(f"   User Data Fields: {len([k for k, v in user_data.items() if v])} fields provided")
        print(f"   - Email (SHA-256): {em_hash[:8]}***" if em_hash else "   - Email: N/A")
        print(f"   - Phone (SHA-256): {ph_hash[:8]}***" if ph_hash else "   - Phone: N/A")
        print(f"   - First Name (SHA-256): {fn_hash[:8]}***" if fn_hash else "")
        print(f"   - Last Name (SHA-256): {ln_hash[:8]}***" if ln_hash else "")
        print(f"   - City (SHA-256): {ct_hash[:8]}***" if ct_hash else "")
        print(f"   - Postcode (SHA-256): {zp_hash[:8]}***" if zp_hash else "")
        print(f"   - State/Region (SHA-256): {st_hash[:8]}***" if st_hash else "")
        print(f"   - Country (SHA-256): {country_hash[:8]}***" if country_hash else "")
        print(f"   - DOB (SHA-256): {db_hash[:8]}***" if db_hash else "")
        print(f"   - Client User Agent: {client_user_agent[:50]}..." if client_user_agent else "")
        print(f"   Custom Data:")
        print(f"   - Value: ${custom_data.get('value', 0)} {custom_data.get('currency', 'CAD')}")
        print(f"   - Lead Status: {custom_data.get('lead_status', 'N/A')}")
        print(f"   - Business Line: {custom_data.get('business_line', 'N/A')}")
        
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        print(f"✅ Meta API Response: {result}")
        return result
    
    except requests.exceptions.RequestException as e:
        print(f"❌ HTTP Error sending to Meta: {str(e)}")
        print(f"   Status: {e.response.status_code if hasattr(e, 'response') else 'N/A'}")
        if hasattr(e, 'response'):
            print(f"   Response: {e.response.text}")
        return {'success': False, 'error': str(e)}
    except Exception as e:
        print(f"❌ Error sending event to Meta: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


# ========== API ENDPOINTS ==========

@app.route('/')
def index():
    """Serve Meta Dashboard as home page"""
    return send_from_directory(app.static_folder, 'meta dashboard.html')

@app.route('/auto')
def auto_dashboard():
    """Serve Auto Dashboard"""
    return send_from_directory(app.static_folder, 'Auto dashboard.html')

@app.route('/signwell-ui')
def signwell_ui():
    """Serve the new purpose-built SignWell Signing UI"""
    return send_from_directory(app.static_folder, 'signwell-ui.html')

@app.route('/document-upload-dashboard')
def document_upload_dashboard():
    """Serve Dashboard-Style Document Upload UI"""
    return send_from_directory(app.static_folder, 'document-upload-dashboard.html')

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'service': 'Meta Lead Dashboard Backend'}), 200


@app.route('/api/streamlit-config', methods=['GET'])
def streamlit_config():
    """Return Streamlit sidecar URLs so HTML pages don't have hardcoded localhost.
    Public endpoint — no auth needed (returns no sensitive data).
    Set STREAMLIT_AUTO_URL / STREAMLIT_TENANT_URL in Railway env vars.
    """
    return jsonify({
        'auto_url': STREAMLIT_AUTO_URL.rstrip('/'),
        'tenant_url': STREAMLIT_TENANT_URL.rstrip('/')
    })


# ========== AUTH ENDPOINTS ==========

@app.route('/login')
def serve_login_page():
    """Serve the login page"""
    return send_from_directory(app.static_folder, 'meta-login.html')

@app.route('/api/login', methods=['POST'])
@limiter.limit('5 per minute')
def api_login():
    """Authenticate user and return JWT token in HTTP-only cookie"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body required'}), 400

        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''

        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400

        # Validate credentials against .env values
        if email != (ADMIN_EMAIL or '').lower():
            return jsonify({'error': 'Invalid email or password'}), 401

        if not bcrypt.checkpw(password.encode('utf-8'), (ADMIN_PASSWORD_HASH or '').encode('utf-8')):
            return jsonify({'error': 'Invalid email or password'}), 401

        # Generate JWT token
        payload = {
            'email': email,
            'iat': datetime.now(timezone.utc),
            'exp': datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
        }
        token = jwt.encode(payload, JWT_SECRET_KEY, algorithm='HS256')

        # Set HTTP-only cookie with the token
        response = jsonify({'success': True, 'email': email})
        response.set_cookie(
            'auth_token',
            token,
            httponly=True,
            secure=COOKIE_SECURE,   # True in prod (HTTPS). Set COOKIE_SECURE=true in Railway.
            samesite='Lax',
            max_age=JWT_EXPIRY_HOURS * 3600,
            path='/'
        )
        print(f"✅ User authenticated: {email}")
        return response, 200

    except Exception as e:
        print(f"❌ Login error: {e}")
        return jsonify({'error': 'Authentication failed'}), 500

@app.route('/api/logout', methods=['POST'])
def api_logout():
    """Clear the auth cookie to log out"""
    response = jsonify({'success': True, 'message': 'Logged out'})
    response.delete_cookie('auth_token', path='/')
    return response, 200

@app.route('/api/verify-token', methods=['GET'])
def verify_token():
    """Check if the current auth token is valid.
    Used by frontend pages to verify authentication status on load."""
    token = request.cookies.get('auth_token')
    if not token:
        return jsonify({'authenticated': False}), 401
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
        return jsonify({'authenticated': True, 'email': payload.get('email')}), 200
    except jwt.ExpiredSignatureError:
        return jsonify({'authenticated': False, 'reason': 'expired'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'authenticated': False, 'reason': 'invalid'}), 401


@app.route('/api/supabase-info', methods=['GET'])
def supabase_info():
    """Check which Supabase database is connected and list tables"""
    try:
        # Extract project reference from URL
        import re
        project_ref = re.search(r'https://([^.]+)\.supabase\.co', SUPABASE_URL)
        project_name = project_ref.group(1) if project_ref else 'Unknown'
        
        # Try to check multiple common tables
        tables_status = {}
        table_names = ['leads', 'auto_data', 'clients_data', 'properties_data', 
                       'tenant_quote_data', 'property_quote_data']
        
        for table_name in table_names:
            try:
                response = supabase.table(table_name).select('*', count='exact').limit(1).execute()
                tables_status[table_name] = {
                    'exists': True,
                    'record_count': response.count if hasattr(response, 'count') else 'unknown',
                    'sample_data': len(response.data) if response.data else 0
                }
            except Exception as e:
                tables_status[table_name] = {
                    'exists': False,
                    'error': str(e)
                }
        
        # Count existing tables
        existing_tables = [t for t, s in tables_status.items() if s.get('exists', False)]
        
        return jsonify({
            'status': 'connected',
            'supabase_url': SUPABASE_URL,
            'project_reference': project_name,
            'service_role_key_configured': bool(SUPABASE_KEY),
            'connection_test': 'successful',
            'tables_found': len(existing_tables),
            'existing_tables_list': existing_tables
        }), 200
    except Exception as e:
        import traceback
        return jsonify({
            'status': 'error',
            'supabase_url': SUPABASE_URL,
            'error': str(e),
            'message': 'Failed to connect to Supabase database'
        }), 500


@app.route('/api/leads/from-facebook', methods=['GET'])
def get_leads_from_facebook():
    """Get ALL leads from Facebook Leads Center (with Supabase fallback)"""
    try:
        print("📱 Fetching leads from Facebook Leads Center...")
        
        if not META_LEAD_FORM_ID:
            print("⚠️ Lead form ID not configured, falling back to Supabase")
            return get_leads_from_supabase()
        
        # Fetch leads from Facebook Leads API for this form
        url = f'{META_BASE_URL}/{META_LEAD_FORM_ID}/leads'
        params = {
            'fields': 'id,created_time,field_data,ad_id,form_id',
            'access_token': META_PAGE_ACCESS_TOKEN,
            'limit': 1000
        }
        
        print(f"📞 Calling Facebook API: {url}")
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        
        facebook_leads = response.json().get('data', [])
        print(f"📋 Facebook API returned {len(facebook_leads)} raw leads")
        
        # Parse all leads from Facebook format
        parsed_leads = []
        for i, fb_lead in enumerate(facebook_leads):
            print(f"🔄 Parsing lead {i+1}/{len(facebook_leads)}")
            parsed = parse_meta_lead(fb_lead)
            print(f"   ✅ Parsed: {parsed.get('name')} | {parsed.get('email')} | {parsed.get('phone')}")
            parsed_leads.append(parsed)
        
        # Sort by date (newest first)
        parsed_leads.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        print(f"✅ Returning {len(parsed_leads)} parsed leads to frontend")
        
        return jsonify({
            'success': True,
            'data': parsed_leads,
            'count': len(parsed_leads),
            'source': 'facebook_leads_center'
        }), 200
        
    except Exception as e:
        print(f"⚠️ Facebook API error: {str(e)}")
        print("📚 Falling back to Supabase database...")
        import traceback
        traceback.print_exc()
        try:
            return get_leads_from_supabase()
        except Exception as fallback_err:
            print(f"❌ Supabase fallback also failed: {fallback_err}")
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(fallback_err)}), 500


def get_leads_from_supabase():
    """Fallback: Get leads from Supabase database"""
    try:
        response = supabase.table('leads').select('*').order('created_at', desc=True).execute()
        leads = response.data if response.data else []
        # Ensure 'name' field exists (construct from first_name/last_name if missing)
        for lead in leads:
            if not lead.get('name'):
                first = lead.get('first_name', '') or ''
                last = lead.get('last_name', '') or ''
                lead['name'] = f"{first} {last}".strip() or lead.get('email') or 'Unknown'
        print(f"📋 Fetched {len(leads)} leads from Supabase")
        return jsonify({
            'success': True,
            'data': leads,
            'count': len(leads),
            'source': 'supabase_database'
        }), 200
    except Exception as e:
        print(f"❌ Supabase error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/manual', methods=['GET'])
def get_manual_leads():
    """Get manual leads from database only"""
    try:
        response = supabase.table('leads').select('*').eq('is_manual', True).order('created_at', desc=True).execute()
        return jsonify({'success': True, 'data': response.data or [], 'count': len(response.data or [])}), 200
    except Exception as e:
        print(f"❌ Error loading manual leads: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads', methods=['GET'])
def get_leads():
    """Get leads from database for general use (signing portal, etc.)"""
    try:
        response = supabase.table('leads').select('*').order('created_at', desc=True).execute()
        leads = response.data if response.data else []
        
        # Normalize data structure for compatibility
        for lead in leads:
            # Ensure name field exists
            if not lead.get('name'):
                first = lead.get('first_name', '') or ''
                last = lead.get('last_name', '') or ''
                lead['name'] = f"{first} {last}".strip() or lead.get('email') or 'Unknown'
            # Add client_name alias for signing portal
            lead['client_name'] = lead.get('name')
        
        print(f"📋 Fetched {len(leads)} leads from database")
        return jsonify(leads), 200
    except Exception as e:
        print(f"❌ Error loading leads: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/leads/sync', methods=['POST', 'GET'])
def sync_leads():
    """Fetch fresh leads from Facebook and save to database"""
    try:
        print("📞 Fetching fresh leads from Facebook API...")
        
        # Fetch from Facebook
        meta_leads = get_leads_from_meta()
        print(f"✅ Fetched {len(meta_leads)} leads from Facebook")
        
        # Parse and save
        new_leads = []
        duplicate_count = 0
        error_count = 0
        
        for meta_lead in meta_leads:
            parsed_lead = parse_meta_lead(meta_lead)
            saved = save_lead_to_supabase(parsed_lead)
            if saved:
                if saved.get('id'):
                    new_leads.append(saved)
                else:
                    duplicate_count += 1
            else:
                error_count += 1
        
        print(f"💾 Saved {len(new_leads)} new leads to database")
        print(f"⏭️  Skipped {duplicate_count} duplicates")
        print(f"❌ {error_count} errors")
        
        return jsonify({
            'success': True,
            'message': f'Synced {len(new_leads)} new leads from Facebook',
            'leads': new_leads,
            'count': len(new_leads),
            'duplicates': duplicate_count,
            'errors': error_count,
            'total_from_facebook': len(meta_leads)
        }), 200
        
    except Exception as e:
        import traceback
        print(f"❌ Error syncing from Facebook: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/leads/debug-meta', methods=['GET'])
def debug_meta_leads():
    """Debug endpoint to see raw Facebook API response"""
    if not ENABLE_DEBUG_ENDPOINTS:
        return jsonify({'error': 'Not found'}), 404
    try:
        meta_leads = get_leads_from_meta()
        # Get first 10 leads with their names
        debug_info = []
        for lead in meta_leads[:10]:
            parsed = parse_meta_lead(lead)
            debug_info.append({
                'id': lead.get('id'),
                'created_time': lead.get('created_time'),
                'name': parsed.get('name'),
                'email': parsed.get('email'),
                'phone': parsed.get('phone')
            })
        
        return jsonify({
            'success': True,
            'total_from_facebook': len(meta_leads),
            'first_10_leads': debug_info
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/test-save-one', methods=['POST'])
def test_save_one_lead():
    """Test saving one lead from Facebook"""
    if not ENABLE_DEBUG_ENDPOINTS:
        return jsonify({'error': 'Not found'}), 404
    try:
        meta_leads = get_leads_from_meta()
        if not meta_leads:
            return jsonify({'success': False, 'error': 'No leads from Facebook'}), 400
        
        # Try to save the first lead
        first_lead = meta_leads[0]
        parsed = parse_meta_lead(first_lead)
        
        # Try to save directly with better error handling
        try:
            if parsed.get('meta_lead_id'):
                existing = supabase.table('leads').select('id').eq('meta_lead_id', parsed.get('meta_lead_id')).execute()
                if existing.data:
                    return jsonify({
                        'success': True,
                        'message': f'Lead already exists: {parsed.get("name")}',
                        'existing': True
                    }), 200
            
            # Try insert
            response = supabase.table('leads').insert(parsed).execute()
            
            if response.data:
                return jsonify({
                    'success': True,
                    'message': f'Successfully saved lead: {parsed.get("name")}',
                    'lead': response.data[0]
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'message': 'No data returned from insert',
                    'lead_data': parsed
                }), 500
                
        except Exception as save_error:
            import traceback
            return jsonify({
                'success': False,
                'message': f'Failed to save lead: {parsed.get("name")}',
                'error': str(save_error),
                'traceback': traceback.format_exc(),
                'lead_data': parsed
            }), 500
            
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


@app.route('/api/leads/check-forms', methods=['GET'])
def check_lead_forms():
    """Check all lead forms on the page"""
    try:
        url = f'{META_BASE_URL}/{META_PAGE_ID}/leadgen_forms'
        params = {
            'fields': 'id,name,status,leads_count',
            'access_token': META_PAGE_ACCESS_TOKEN
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        return jsonify({
            'success': True,
            'forms': data.get('data', []),
            'current_form_id': META_LEAD_FORM_ID
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/<lead_id>/sync-event', methods=['POST'])
def sync_lead_event(lead_id):
    """Send lead qualification event to Meta Event Manager"""
    try:
        data = request.get_json()
        event_type = data.get('event_type', None)  # Can override, or auto-detect
        
        # Get lead from database - check if it's a UUID or Facebook lead ID
        lead = None
        is_uuid = '-' in lead_id and len(lead_id) == 36  # UUID format check
        
        if is_uuid:
            # Query by Supabase UUID
            response = supabase.table('leads').select('*').eq('id', lead_id).execute()
            lead = response.data[0] if response.data else None
        else:
            # Query by meta_lead_id (numeric ID from Facebook)
            response = supabase.table('leads').select('*').eq('meta_lead_id', lead_id).execute()
            lead = response.data[0] if response.data else None
        
        if not lead:
            return jsonify({'success': False, 'error': 'Lead not found'}), 404
        
        # Auto-detect event type based on lead status if not provided
        if not event_type:
            if lead.get('is_auto_qualified'):
                event_type = 'QualifiedLead'  # Qualified = 5+ years experience
            else:
                # Don't send events for unqualified leads - user feedback: only send QualifiedLead and Purchase
                return jsonify({
                    'success': False, 
                    'error': 'Lead is not qualified (< 5 years driving experience). Only qualified leads and purchases are synced to Meta.',
                    'is_qualified': False
                }), 400
        
        # Log sync initiation
        print(f"\n{'='*60}")
        print(f"🔔 SYNCING LEAD TO FACEBOOK EVENT MANAGER")
        print(f"{'='*60}")
        print(f"📋 Lead ID: {lead_id}")
        print(f"👤 Name: {lead.get('name', 'N/A')}")
        print(f"📧 Email: {lead.get('email', 'N/A')}")
        print(f"📞 Phone: {lead.get('phone', 'N/A')}")
        print(f"💰 Premium: ${lead.get('premium', 0)}")
        print(f"✅ Qualified: {lead.get('is_auto_qualified', False)}")
        print(f"📍 Event Type: {event_type}")
        print(f"⏰ Timestamp: {datetime.now(timezone.utc).isoformat()}")
        print(f"🎯 Destination Pixel ID: {FB_PIXEL_ID}")
        print(f"🔗 Events Manager URL: https://business.facebook.com/events_manager2/list/pixel/{FB_PIXEL_ID}/test_events")
        
        # Enrich with auto_data (parsed DASH/MVR info) for better matching
        city = ''
        postal_code = ''
        date_of_birth = ''
        state = 'on'  # Default to Ontario
        
        email = lead.get('email', '').strip().lower()
        if email:
            try:
                auto_result = supabase.table('auto_data').select('auto_data').eq('email', email).limit(1).execute()
                if auto_result.data and len(auto_result.data) > 0:
                    auto_data = auto_result.data[0].get('auto_data', {})
                    drivers = auto_data.get('drivers', [])
                    if drivers and len(drivers) > 0:
                        driver = drivers[0]
                        
                        # Extract DOB
                        date_of_birth = driver.get('personalDob', '')
                        
                        # Parse address to extract city and postal code
                        personal_address = driver.get('personalAddress', '')
                        if personal_address:
                            addr_parts = personal_address.strip().split()
                            if len(addr_parts) >= 3:
                                # Last part is usually postal code (e.g., L6S3S2)
                                potential_postal = addr_parts[-1]
                                if len(potential_postal) == 6 and potential_postal[0].isalpha():
                                    postal_code = potential_postal
                                # Third from last is usually city
                                if len(addr_parts) >= 3:
                                    city = addr_parts[-3]
                                # Second from last is province
                                if len(addr_parts) >= 2:
                                    state = addr_parts[-2].lower()
                        
                        print(f"📋 Enriched from auto_data: City={city}, Postal={postal_code}, DOB={date_of_birth}, State={state}")
            except Exception as e:
                print(f"⚠️ Could not fetch auto_data for enrichment: {str(e)}")
        
        # Send to Meta with ad attribution context and enriched data
        event_data = {
            'email': lead.get('email', ''),
            'phone': lead.get('phone', ''),
            'name': lead.get('name', ''),
            'premium': lead.get('premium', 0),
            'meta_lead_id': lead.get('meta_lead_id', ''),  # Facebook Lead ID for better matching
            # Enriched personal data from DASH/MVR
            'city': city,
            'zip': postal_code,
            'state': state,
            'date_of_birth': date_of_birth,
            'country': 'ca',  # Canada
            'external_id': str(lead_id),  # Lead ID as external ID
            # Ad attribution for optimization
            'ad_id': lead.get('ad_id', ''),
            'form_id': lead.get('form_id', ''),
            'adset_id': lead.get('adset_id', ''),
            'campaign_id': lead.get('campaign_id', ''),
            # Test mode
            'test_event_code': data.get('test_event_code', '')
        }
        
        print(f"📤 Sending to Meta Conversions API...")
        result = send_event_to_meta(lead['id'], event_type, event_data)
        
        # Update lead sync timestamp and status
        # Meta returns 'events_received' field when successful
        sync_status = 'sent' if result and result.get('events_received', 0) > 0 else 'failed'
        supabase.table('leads').update({
            'last_sync': datetime.now(timezone.utc).isoformat(),
            'sync_status': sync_status
        }).eq('id', lead['id']).execute()
        
        # Log sync event to database
        supabase.table('sync_events').insert({
            'lead_id': lead['id'],
            'event_type': event_type,
            'meta_response': result,
            'created_at': datetime.now(timezone.utc).isoformat()
        }).execute()
        
        # Log confirmation
        print(f"{'─'*60}")
        print(f"✅ SYNC CONFIRMATION")
        print(f"{'─'*60}")
        print(f"✨ Lead '{lead.get('name')}' successfully queued for Meta Event Manager")
        print(f"📊 Meta API Response: {result}")
        print(f"💾 Sync Status: {sync_status}")
        print(f"🔗 Lead saved to sync_events table for tracking")
        print(f"⏱️  Confirmed at: {datetime.now(timezone.utc).isoformat()}")
        print(f"{'='*60}\n")
        
        # Build list of fields that were sent to Meta (non-empty ones)
        fields_sent = {
            'email': bool(event_data.get('email')),
            'phone': bool(event_data.get('phone')),
            'name': bool(event_data.get('name')),
            'city': bool(city),
            'postal_code': bool(postal_code),
            'state': bool(state),
            'date_of_birth': bool(date_of_birth),
            'country': True,
            'meta_lead_id': bool(lead.get('meta_lead_id'))
        }
        
        return jsonify({
            'success': True,
            'message': f'Event "{event_type}" sent to Meta Event Manager',
            'meta_response': result,
            'lead_name': lead.get('name'),
            'lead_email': lead.get('email'),
            'sync_timestamp': datetime.now(timezone.utc).isoformat(),
            'data_sent_to_meta': {
                'email': event_data.get('email', ''),
                'phone': event_data.get('phone', ''),
                'name': event_data.get('name', ''),
                'city': city,
                'postal_code': postal_code,
                'state': state,
                'date_of_birth': date_of_birth,
                'country': 'ca',
                'meta_lead_id': lead.get('meta_lead_id', ''),
                'fields_sent': fields_sent
            }
        }), 200
    
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"❌ SYNC FAILED")
        print(f"{'='*60}")
        print(f"Error syncing lead event {lead_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'='*60}\n")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/create', methods=['POST'])
def create_lead():
    """Create manual lead"""
    try:
        data = request.get_json()
        
        new_lead = {
            'name': data.get('name', ''),
            'phone': data.get('phone', ''),
            'email': data.get('email', ''),
            'type': data.get('type', 'general'),
            'status': data.get('status', 'New Lead'),
            'potential_status': data.get('potential_status', 'qualified'),
            'notes': data.get('notes', ''),
            'is_manual': True,
            'premium': float(data.get('premium', 0)),
            'renewal_date': data.get('renewal_date'),
            'insurance_type': data.get('insurance_type'),
            'policy_term': data.get('policy_term'),
            'visa_type': data.get('visa_type'),
            'coverage': float(data.get('coverage', 0)) if data.get('coverage') else None,
            'trip_start': data.get('trip_start'),
            'trip_end': data.get('trip_end'),
            'sync_status': 'pending',
            'sync_signal': 'green',
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        saved = save_lead_to_supabase(new_lead)
        return jsonify({'success': True, 'data': saved}), 201
    
    except Exception as e:
        print(f"Error creating lead: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/<lead_id>/full-data', methods=['GET'])
def get_lead_full_data(lead_id):
    """Get complete lead data including parsed DASH/MVR info from auto_data table"""
    try:
        lead = None
        
        # Check if lead_id looks like a UUID (36 chars with dashes)
        is_uuid = len(lead_id) == 36 and lead_id.count('-') == 4
        
        # Try to get lead from database by UUID first (only if it looks like a UUID)
        if is_uuid:
            try:
                lead_result = supabase.table('leads').select('*').eq('id', lead_id).execute()
                lead = lead_result.data[0] if lead_result.data else None
            except Exception as e:
                print(f"⚠️ UUID query failed: {str(e)}")
        
        # If not found by UUID, try by meta_lead_id
        if not lead:
            print(f"🔍 Trying meta_lead_id: {lead_id}")
            lead_result = supabase.table('leads').select('*').eq('meta_lead_id', lead_id).execute()
            lead = lead_result.data[0] if lead_result.data else None
        
        # If still not found, check if email was provided as query param
        email_param = request.args.get('email', '').strip().lower()
        
        if not lead and not email_param:
            return jsonify({'success': False, 'error': 'Lead not found and no email provided'}), 404
        
        # If we have a lead, use its email; otherwise use the provided email
        email = lead.get('email', '').strip().lower() if lead else email_param
        
        # Create a lead object if we only have email (for display purposes)
        if not lead:
            lead = {'email': email_param, 'name': '', 'phone': '', 'status': 'Unknown'}
        
        # Initialize parsed data fields
        parsed_data = {
            'first_name': '',
            'middle_name': '',
            'last_name': '',
            'full_name': '',
            'address': '',
            'city': '',
            'postal_code': '',
            'date_of_birth': '',
            'gender': '',
            'marital_status': '',
            'mvr_issue_date': '',
            'has_parsed_data': False
        }
        
        # Look up auto_data by email
        if lead.get('email'):
            email = lead['email'].strip().lower()
            print(f"🔍 Looking up auto_data for email: {email}")
            
            auto_result = supabase.table('auto_data').select('auto_data').eq('email', email).limit(1).execute()
            
            if auto_result.data and len(auto_result.data) > 0:
                auto_data = auto_result.data[0].get('auto_data', {})
                print(f"📋 Found auto_data with keys: {list(auto_data.keys()) if isinstance(auto_data, dict) else 'Not a dict'}")
                
                # Extract from drivers array
                drivers = auto_data.get('drivers', [])
                if drivers and len(drivers) > 0:
                    driver = drivers[0]
                    print(f"👤 Driver data keys: {list(driver.keys())[:10]}")
                    
                    # Parse full name into parts
                    personal_name = driver.get('personalName', '') or driver.get('mainName', '')
                    if personal_name:
                        name_parts = personal_name.strip().split()
                        parsed_data['full_name'] = personal_name
                        parsed_data['first_name'] = name_parts[0] if name_parts else ''
                        parsed_data['last_name'] = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''
                    
                    # Parse address to extract city and postal
                    personal_address = driver.get('personalAddress', '')
                    if personal_address:
                        parsed_data['address'] = personal_address
                        # Canadian addresses often end with: CITY PROVINCE POSTAL
                        addr_parts = personal_address.strip().split()
                        if len(addr_parts) >= 3:
                            # Last part is usually postal code (e.g., L6S3S2)
                            potential_postal = addr_parts[-1]
                            if len(potential_postal) == 6 and potential_postal[0].isalpha():
                                parsed_data['postal_code'] = potential_postal
                                # Third from last is usually city
                                if len(addr_parts) >= 3:
                                    parsed_data['city'] = addr_parts[-3]
                    
                    # Other fields
                    parsed_data['date_of_birth'] = driver.get('personalDob', '')
                    parsed_data['gender'] = driver.get('personalGender', '')
                    parsed_data['marital_status'] = driver.get('personalMaritalStatus', '')
                    parsed_data['mvr_issue_date'] = driver.get('mvrIssue', '')
                    
                    parsed_data['has_parsed_data'] = True
                    print(f"✅ Extracted parsed data: {parsed_data}")
                else:
                    print(f"⚠️ No drivers array in auto_data")
            else:
                print(f"⚠️ No auto_data found for email: {email}")
        
        return jsonify({
            'success': True,
            'lead': lead,
            'parsed_data': parsed_data
        }), 200
        
    except Exception as e:
        print(f"❌ Error getting full lead data: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/<lead_id>', methods=['PUT'])
def update_lead(lead_id):
    """Update lead"""
    try:
        data = request.get_json()
        
        # Whitelist allowed fields to prevent overwriting id, created_at, etc.
        ALLOWED_FIELDS = {
            'name', 'phone', 'email', 'message', 'status', 'type',
            'potential_status', 'premium', 'sync_status', 'sync_signal',
            'notes', 'coverage', 'insuranceType', 'visaType',
            'trip_start', 'trip_end', 'is_auto_qualified',
            'driver_license_received', 'reminder_date', 'reminder_note'
        }
        filtered_data = {k: v for k, v in data.items() if k in ALLOWED_FIELDS}
        
        if not filtered_data:
            return jsonify({'success': False, 'error': 'No valid fields to update'}), 400
        
        response = supabase.table('leads').update(filtered_data).eq('id', lead_id).execute()
        
        return jsonify({'success': True, 'data': response.data[0] if response.data else None}), 200
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/<lead_id>', methods=['DELETE'])
def delete_lead(lead_id):
    """Delete lead"""
    try:
        # Delete from clients_data and properties_data first
        supabase.table('clients_data').delete().eq('lead_id', lead_id).execute()
        supabase.table('properties_data').delete().eq('lead_id', lead_id).execute()
        # Then delete from leads
        supabase.table('leads').delete().eq('id', lead_id).execute()
        return jsonify({'success': True, 'message': 'Lead and all related data deleted'}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/clear-all', methods=['POST'])
def clear_all_leads():
    """Delete all leads from database"""
    try:
        # Get all lead IDs first
        all_leads = supabase.table('leads').select('id').execute()
        
        if all_leads.data:
            # Delete each lead
            for lead in all_leads.data:
                supabase.table('leads').delete().eq('id', lead['id']).execute()
            
            return jsonify({
                'success': True, 
                'message': f'Cleared {len(all_leads.data)} leads from database'
            }), 200
        else:
            return jsonify({
                'success': True, 
                'message': 'Database already empty'
            }), 200
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/leads/<lead_id>/signal', methods=['POST'])
def update_signal(lead_id):
    """Update lead signal (green/red)"""
    try:
        data = request.get_json()
        signal = data.get('signal', 'green')  # 'green' or 'red'
        
        supabase.table('leads').update({
            'sync_signal': signal,
            'potential_status': 'qualified' if signal == 'green' else 'not-qualified'
        }).eq('id', lead_id).execute()
        
        return jsonify({'success': True, 'message': f'Signal updated to {signal}'}), 200
    
    except Exception as e:
        print(f"Error updating signal: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """Meta webhook endpoint for real-time Facebook Lead Ads via leadgen webhook
    
    FLOW:
    1. Receive leadgen event from Facebook webhook
    2. Fetch full lead details from Graph API
    3. PUSH IMMEDIATELY to all connected Meta Dashboard clients via WebSocket
    4. Then save to database (storage only)
    """
    
    if request.method == 'GET':
        # Webhook verification
        hub_challenge = request.args.get('hub.challenge')
        hub_verify_token = request.args.get('hub.verify_token')
        
        if hub_verify_token != META_WEBHOOK_VERIFY_TOKEN:
            print(f"❌ Webhook verification failed: {hub_verify_token} != {META_WEBHOOK_VERIFY_TOKEN}")
            return 'Invalid verify token', 403
        
        print(f"✅ Webhook verification successful")
        return hub_challenge, 200
    
    elif request.method == 'POST':
        # Verify signature
        hub_signature = request.headers.get('X-Hub-Signature-256', '')
        if not verify_meta_webhook(request.data, hub_signature):
            print(f"❌ Webhook signature verification failed")
            return 'Invalid signature', 403
        
        data = request.get_json()
        print(f"📨 Webhook POST received")
        
        # Process leadgen event (new lead created on Facebook)
        entry = data.get('entry', [{}])[0]
        changes = entry.get('changes', [])
        
        for change in changes:
            field = change.get('field')
            value = change.get('value', {})
            
            # Handle leadgen_id webhook event
            if field == 'leadgen':
                leadgen_id = value.get('leadgen_id')
                print(f"📝 New leadgen event - ID: {leadgen_id}")
                
                if leadgen_id:
                    # Fetch full lead details from Meta Graph API
                    lead_details = fetch_leadgen_details(leadgen_id)
                    
                    if lead_details:
                        # Parse lead data
                        parsed_lead = parse_meta_lead(lead_details)
                        
                        # STEP 1: IMMEDIATELY PUSH TO ALL CONNECTED DASHBOARD CLIENTS
                        print(f"⚡ PUSHING lead to connected dashboard clients: {parsed_lead.get('name')}")
                        socketio.emit('new_lead', {
                            'lead': parsed_lead,
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                            'source': 'webhook'
                        }, room='dashboard')
                        
                        # STEP 2: Save to database (storage only, not for display)
                        saved = save_lead_to_supabase(parsed_lead)
                        
                        if saved:
                            print(f"✅ Lead saved to database from webhook: {parsed_lead.get('name')}")
                            return jsonify({'success': True, 'lead_id': saved.get('id')}), 200
                        else:
                            print(f"✅ Lead pushed to dashboard (database save returned None)")
                            return jsonify({'success': True}), 200
                    else:
                        print(f"❌ Failed to fetch lead details for {leadgen_id}")
                        return jsonify({'success': False, 'error': 'Failed to fetch lead details'}), 500
            
            # Handle messaging webhook events (backward compatibility)
            elif field == 'messages':
                messaging = value.get('messaging', [])
                for msg in messaging:
                    if msg.get('message', {}).get('is_echo'):
                        continue
                    
                    sender_id = msg.get('sender', {}).get('id')
                    message = msg.get('message', {}).get('text', '')
                    
                    lead_data = {
                        'meta_user_id': sender_id,
                        'message': message,
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'status': 'New Lead',
                        'is_manual': False
                    }
                    save_lead_to_supabase(lead_data)
        
        return jsonify({'success': True}), 200


# ========== WEBSOCKET ENDPOINTS ==========

@socketio.on('connect')
def handle_connect():
    """Handle client connection to WebSocket"""
    print(f"👤 Client connected: {request.sid}")
    emit('connection_response', {'data': 'Connected to real-time lead server'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection from WebSocket"""
    print(f"👤 Client disconnected: {request.sid}")

@socketio.on('join_dashboard')
def on_join_dashboard():
    """Client joins the dashboard room to receive live lead updates"""
    join_room('dashboard')
    print(f"📊 Client joined dashboard room: {request.sid}")
    emit('joined_dashboard', {'data': 'You are now receiving live lead updates'})



def fetch_leadgen_details(leadgen_id):
    """Fetch full lead details from Facebook Graph API using leadgen_id"""
    try:
        url = f'{META_BASE_URL}/{leadgen_id}'
        
        params = {
            'fields': 'id,created_time,field_data,ad_id,form_id,adset_id,campaign_id',
            'access_token': META_PAGE_ACCESS_TOKEN
        }
        
        print(f"🔍 Fetching leadgen details for {leadgen_id}")
        response = requests.get(url, params=params, timeout=30)
        print(f"📡 Graph API Response Status: {response.status_code}")
        response.raise_for_status()
        
        lead_data = response.json()
        print(f"✅ Lead details fetched: {lead_data}")
        return lead_data
        
    except Exception as e:
        print(f"❌ Error fetching leadgen details: {str(e)}")
        if hasattr(e, 'response'):
            print(f"❌ Meta API Error Response: {e.response.text if e.response else 'No response'}")
        return None


# ========== PDF PARSING ENDPOINT ==========

@app.route('/api/parse-mvr', methods=['POST'])
def parse_mvr():
    """Parse uploaded MVR PDF and extract driver information"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Empty filename'}), 400
        
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'success': False, 'error': 'Only PDF files are supported'}), 400
        
        # Read file content
        pdf_content = file.read()
        
        # Parse the PDF
        result = parse_mvr_pdf(pdf_content)
        
        if not result['success']:
            return jsonify(result), 400
        
        # CRITICAL: Verify policy1_vehicles is in the response before sending to client
        print(f"\n[API] /parse-mvr endpoint response verification:")
        print(f"[API] - 'policy1_vehicles' in result['data']: {'policy1_vehicles' in result['data']}")
        if 'policy1_vehicles' in result['data']:
            print(f"[API] - result['data']['policy1_vehicles']: {result['data']['policy1_vehicles']}")
        
        # Return extracted data
        return jsonify({
            'success': True,
            'data': result['data']
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500


@app.route('/api/parse-dash', methods=['POST'])
def parse_dash():
    """Parse uploaded DASH PDF and extract driver information"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Empty filename'}), 400
        
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'success': False, 'error': 'Only PDF files are supported'}), 400
        
        # Read file content
        pdf_content = file.read()
        
        # Parse the PDF
        result = parse_dash_pdf(pdf_content)
        
        if not result['success']:
            return jsonify(result), 400
        
        # Return extracted data AND raw text for debugging
        return jsonify({
            'success': True,
            'data': result['data'],
            'raw_text': result['raw_text'][:1000]  # First 1000 chars for debugging
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500


@app.route('/api/parse-quote', methods=['POST'])
def parse_quote():
    """Parse uploaded Auto Quote PDF or JSON and extract coverage information"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'success': False, 'error': 'Empty filename'}), 400

        filename = file.filename.lower()
        
        # Handle JSON files
        if filename.endswith('.json'):
            try:
                json_content = file.read().decode('utf-8')
                json_data = json.loads(json_content)
                
                # Extract data from JSON (could be drivers array or single object)
                data = {}
                if isinstance(json_data, list) and len(json_data) > 0:
                    data = json_data[0]  # Use first driver if array
                elif isinstance(json_data, dict):
                    data = json_data
                
                return jsonify({
                    'success': True,
                    'data': data
                }), 200
            except json.JSONDecodeError:
                return jsonify({
                    'success': False,
                    'error': 'Invalid JSON file format'
                }), 400
        
        # Handle PDF files
        elif filename.endswith('.pdf'):
            pdf_content = file.read()
            result = parse_quote_pdf(pdf_content)

            if not result['success']:
                return jsonify(result), 400

            return jsonify({
                'success': True,
                'data': result['data']
            }), 200
        
        else:
            return jsonify({'success': False, 'error': 'Only PDF and JSON files are supported'}), 400

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/parse-property-quote', methods=['POST'])
def parse_property_quote():
    """Parse uploaded Property Quote PDF and extract coverage information"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'success': False, 'error': 'Empty filename'}), 400

        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'success': False, 'error': 'Only PDF files are supported'}), 400

        pdf_content = file.read()

        result = parse_property_quote_pdf(pdf_content)

        if not result['success']:
            return jsonify(result), 400

        print(f"[API] parse_property_quote_pdf returned {len(result['data'])} keys")
        print(f"[API] Parsing method: {result.get('method', 'unknown')}")
        print(f"[API] Data keys: {list(result['data'].keys())}")
        prefixed_keys = [k for k in result['data'].keys() if '_coverage' in k or '_deductible' in k]
        print(f"[API] Prefixed keys: {prefixed_keys}")

        return jsonify({
            'success': True,
            'data': result['data'],
            'method': result.get('method', 'unknown')
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500


@app.route('/api/export-pdf', methods=['POST'])
@limiter.limit('10 per minute')
def export_pdf():
    """Generate PDF from HTML content - OPTIMIZED FOR SPEED"""
    try:
        from flask import send_file
        from io import BytesIO
        import tempfile
        import os
        
        # Get HTML content from request
        html_content = request.form.get('html_content')
        
        if not html_content:
            return jsonify({'success': False, 'error': 'No HTML content provided'}), 400

        # Prevent memory exhaustion from oversized payloads
        if len(html_content) > 500_000:
            return jsonify({'success': False, 'error': 'HTML content too large (max 500KB)'}), 400
        
        # Use Playwright to render HTML to PDF with all CSS preserved
        from playwright.sync_api import sync_playwright
        
        # Create temporary HTML file with complete styling
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
            temp_html_path = f.name
            # Wrap with complete styling for professional PDF output
            full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Cover Page</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
        
        /* Base reset */
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html {{ font-size: 11px !important; }} /* Scale down base font for professional PDF */
        body {{ 
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; 
            color: #1f2937; 
            font-size: 11px;
            line-height: 1.4;
            background: white !important;
            margin: 0 !important;
            padding: 0 !important;
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
        }}
        
        /* Sheet styles - Letter size with professional margins */
        .sheet {{ 
            background: white; 
            margin: 0 !important; 
            padding: 0.2in 0.1in; 
            width: 100%; 
            min-height: auto;
            overflow: visible;
        }}
        .sheet:last-child {{ page-break-after: avoid; }}
        
        /* Professional typography - scaled down */
        h1 {{ font-size: 16px !important; font-weight: 700; margin: 0; }}
        h2 {{ font-size: 13px !important; font-weight: 700; margin: 0; }}
        h3 {{ font-size: 11px !important; font-weight: 700; margin: 0; }}
        h4 {{ font-size: 10px !important; font-weight: 700; margin: 0; }}
        p {{ margin: 0; line-height: 1.4; font-size: 10px; }}
        
        /* Explicit text sizes for PDF - smaller for professional look */
        .text-xs {{ font-size: 9px !important; }}
        .text-sm {{ font-size: 10px !important; }}
        .text-base {{ font-size: 11px !important; }}
        .text-lg {{ font-size: 12px !important; }}
        .text-xl {{ font-size: 14px !important; }}
        .text-2xl {{ font-size: 16px !important; }}
        .text-\\[8px\\] {{ font-size: 7px !important; }}
        .text-\\[9px\\] {{ font-size: 8px !important; }}
        .text-\\[10px\\] {{ font-size: 9px !important; }}
        .text-\\[11px\\] {{ font-size: 10px !important; }}
        
        /* Ensure all text content is visible */
        span {{ display: inline; }}
        
        /* Hide form controls and interactive elements */
        input, select, textarea, button {{ display: none !important; }}
        .delete-coverage-btn {{ display: none !important; }}
        .sidebar {{ display: none !important; }}
        
        /* Preserve flex layouts */
        .flex {{ display: flex; }}
        .flex-col {{ flex-direction: column; }}
        .items-center {{ align-items: center; }}
        .items-start {{ align-items: flex-start; }}
        .justify-between {{ justify-content: space-between; }}
        .gap-1 {{ gap: 0.2rem; }}
        .gap-2 {{ gap: 0.35rem; }}
        .gap-4 {{ gap: 0.6rem; }}
        .gap-x-6 {{ column-gap: 1rem; }}
        .gap-y-2 {{ row-gap: 0.35rem; }}
        .space-y-2 > * + * {{ margin-top: 0.35rem; }}
        .space-y-3 > * + * {{ margin-top: 0.5rem; }}
        
        /* Font weights */
        .font-bold {{ font-weight: 700; }}
        .font-semibold {{ font-weight: 600; }}
        .font-medium {{ font-weight: 500; }}
        
        /* Colors - ensure visibility */
        .text-gray-500 {{ color: #6b7280; }}
        .text-gray-600 {{ color: #4b5563; }}
        .text-gray-700 {{ color: #374151; }}
        .text-gray-900 {{ color: #111827; }}
        .text-black {{ color: #000 !important; }}
        .text-white {{ color: #fff !important; }}
        .text-blue-900 {{ color: #1e3a5f; }}
        .bg-blue-900 {{ background-color: #1e3a5f !important; -webkit-print-color-adjust: exact; }}
        .bg-slate-50 {{ background-color: #f8fafc !important; }}
        .bg-blue-50 {{ background-color: #eff6ff !important; }}
        .bg-gray-50 {{ background-color: #f9fafb !important; }}
        .bg-gray-100 {{ background-color: #f3f4f6 !important; }}
        .bg-white {{ background-color: #fff !important; }}
        
        /* Borders */
        .border {{ border-width: 1px; border-style: solid; }}
        .border-2 {{ border-width: 2px; border-style: solid; }}
        .border-t {{ border-top-width: 1px; border-top-style: solid; }}
        .border-b {{ border-bottom-width: 1px; border-bottom-style: solid; }}
        .border-b-2 {{ border-bottom-width: 2px; border-bottom-style: solid; }}
        .border-l-4 {{ border-left-width: 4px; border-left-style: solid; }}
        .border-r {{ border-right-width: 1px; border-right-style: solid; }}
        .border-black {{ border-color: #000; }}
        .border-gray-200 {{ border-color: #e5e7eb; }}
        .border-gray-300 {{ border-color: #d1d5db; }}
        .border-gray-400 {{ border-color: #9ca3af; }}
        .border-blue-100 {{ border-color: #dbeafe; }}
        .border-blue-900 {{ border-color: #1e3a5f; }}
        .border-dashed {{ border-style: dashed; }}
        .rounded {{ border-radius: 0.2rem; }}
        .rounded-md {{ border-radius: 0.3rem; }}
        
        /* Spacing - tighter for PDF */
        .p-2 {{ padding: 0.35rem; }}
        .p-2\\.5 {{ padding: 0.4rem; }}
        .p-3 {{ padding: 0.5rem; }}
        .p-4 {{ padding: 0.65rem; }}
        .px-2 {{ padding-left: 0.35rem; padding-right: 0.35rem; }}
        .px-3 {{ padding-left: 0.5rem; padding-right: 0.5rem; }}
        .py-1 {{ padding-top: 0.2rem; padding-bottom: 0.2rem; }}
        .py-2 {{ padding-top: 0.35rem; padding-bottom: 0.35rem; }}
        .mb-0\\.5 {{ margin-bottom: 0.1rem; }}
        .mb-1 {{ margin-bottom: 0.2rem; }}
        .mb-1\\.5 {{ margin-bottom: 0.3rem; }}
        .mb-2 {{ margin-bottom: 0.35rem; }}
        .mb-3 {{ margin-bottom: 0.5rem; }}
        .mb-4 {{ margin-bottom: 0.65rem; }}
        .mt-2 {{ margin-top: 0.35rem; }}
        .mt-4 {{ margin-top: 0.65rem; }}
        .pb-1 {{ padding-bottom: 0.2rem; }}
        .-mx-2 {{ margin-left: -0.35rem; margin-right: -0.35rem; }}
        
        /* Width */
        .w-full {{ width: 100%; }}
        .w-1\\/2 {{ width: 50%; }}
        .w-1\\/3 {{ width: 33.333%; }}
        .w-2\\/3 {{ width: 66.666%; }}
        
        /* Grid */
        .grid {{ display: grid; }}
        .grid-cols-2 {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        .col-span-2 {{ grid-column: span 2; }}
        
        /* Text transform */
        .uppercase {{ text-transform: uppercase; }}
        .underline {{ text-decoration: underline; }}
        .tracking-wider {{ letter-spacing: 0.05em; }}
        
        /* Editable field appearance for PDF */
        .editable-field {{ 
            display: inline !important;
            font-weight: 700;
        }}
        .liability-amount {{
            display: inline !important;
        }}
        
        @media print {{ 
            body {{ background: white !important; padding: 0 !important; }} 
            .sheet {{ box-shadow: none !important; margin: 0 !important; }} 
            * {{ -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }}
        }}
    </style>
</head>
<body>
{html_content}
</body>
</html>"""
            f.write(full_html)
        
        try:
            # Fast Playwright configuration
            with sync_playwright() as p:
                # Launch browser in headless mode (already default)
                browser = p.chromium.launch(headless=True, args=['--disable-extensions'])
                
                # Create page with optimized settings
                page = browser.new_page(
                    viewport={'width': 816, 'height': 1056}
                )

                # SSRF protection: only allow known CDN domains for outbound requests.
                # Blocks attempts to reach internal metadata endpoints (169.254.x.x),
                # Railway internal services, or any other non-CDN host.
                _PDF_ALLOWED_HOSTS = {
                    'cdn.tailwindcss.com',
                    'cdnjs.cloudflare.com',
                    'fonts.googleapis.com',
                    'fonts.gstatic.com',
                }

                def _pdf_route_handler(route):
                    from urllib.parse import urlparse
                    parsed = urlparse(route.request.url)
                    if parsed.scheme in ('data', 'file', 'about', 'blob'):
                        route.continue_()
                    elif parsed.scheme in ('http', 'https'):
                        host = (parsed.hostname or '').lower()
                        if any(host == d or host.endswith('.' + d) for d in _PDF_ALLOWED_HOSTS):
                            route.continue_()
                        else:
                            route.abort()
                    else:
                        route.abort()

                page.route('**/*', _pdf_route_handler)

                # Load HTML - wait for networkidle to ensure styles load
                page.goto(f'file://{temp_html_path}', wait_until='networkidle', timeout=30000)
                
                # Wait for fonts and styles to fully load
                page.wait_for_timeout(1500)
                
                # Generate PDF with proper margins for professional look
                pdf_bytes = page.pdf(
                    format='Letter',
                    print_background=True,
                    margin={'top': '0.4in', 'bottom': '0.4in', 'left': '0.5in', 'right': '0.5in'},
                    scale=0.95,
                    prefer_css_page_size=False
                )
                
                # Clean up immediately
                page.close()
                browser.close()
                
                # Return PDF
                pdf_buffer = BytesIO(pdf_bytes)
                pdf_buffer.seek(0)
                
                return send_file(
                    pdf_buffer,
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name='Auto_CoverPage.pdf'
                )
        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_html_path)
            except Exception:
                pass
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'PDF generation failed: {str(e)}'
        }), 500


@app.route('/api/save-client', methods=['POST'])
def save_client():
    """Save complete client data to Supabase linked to a lead"""
    try:
        data = request.json
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        print(f"💾 Saving client data to Supabase...")
        print(f"📊 Data received keys: {list(data.keys())}")
        print(f"📊 Received drivers: {len(data.get('drivers', []))} driver(s)")
        
        # Get email/phone/name to identify the lead
        email = None
        phone = None
        name = None
        
        if data.get('drivers') and len(data['drivers']) > 0:
            driver = data['drivers'][0]
            print(f"🔍 First driver keys: {list(driver.keys())[:5]}...")
            email = driver.get('personalEmail')
            phone = driver.get('personalMobile')
            name = driver.get('personalName') or driver.get('mainName')
            print(f"✓ Extracted - Email: {email}, Phone: {phone}, Name: {name}")
        else:
            print(f"⚠️ No drivers data found in request")
        
        print(f"📋 Lead info - Name: {name}, Email: {email}, Phone: {phone}")
        
        # Find lead by email, phone, or name
        lead_id = None
        
        # Try email first
        if email:
            try:
                result = supabase.table('leads').select('id').eq('email', email).limit(1).execute()
                if result.data and len(result.data) > 0:
                    lead_id = result.data[0]['id']
                    print(f"✅ Found lead by email {email}: {lead_id}")
            except Exception as e:
                print(f"⚠️ Error finding lead by email: {str(e)}")
        
        # Try phone if email didn't work
        if not lead_id and phone:
            try:
                result = supabase.table('leads').select('id').eq('phone', phone).limit(1).execute()
                if result.data and len(result.data) > 0:
                    lead_id = result.data[0]['id']
                    print(f"✅ Found lead by phone {phone}: {lead_id}")
            except Exception as e:
                print(f"⚠️ Error finding lead by phone: {str(e)}")
        
        # Try name if still not found
        if not lead_id and name:
            try:
                result = supabase.table('leads').select('id').eq('name', name).limit(1).execute()
                if result.data and len(result.data) > 0:
                    lead_id = result.data[0]['id']
                    print(f"✅ Found lead by name {name}: {lead_id}")
            except Exception as e:
                print(f"⚠️ Error finding lead by name: {str(e)}")
        
        if not lead_id:
            print(f"⚠️ No lead found with email={email}, phone={phone}, name={name}")
        
        # Prepare data for storage - only include columns that exist in table
        save_data = {
            'email': email,
            'drivers': data.get('drivers', []),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Add lead_id if found
        if lead_id:
            save_data['lead_id'] = lead_id
        
        print(f"💾 INSERT/UPDATE STEP - lead_id: {lead_id}, email: {email}, phone: {phone}")
        print(f"📦 Data to save keys: {list(save_data.keys())}")
        print(f"📦 Drivers count: {len(save_data.get('drivers', []))}")
        
        # Insert or update in clients_data table
        if email:
            print(f"💡 Saving client data by email: {email} (lead_id: {lead_id})")
            try:
                # Prefer to update by lead_id if found, else by email
                if lead_id:
                    result = supabase.table('clients_data').select('id').eq('lead_id', lead_id).limit(1).execute()
                    if result.data and len(result.data) > 0:
                        print(f"🔄 Existing record found for lead {lead_id}, updating...")
                        save_result = supabase.table('clients_data').update(save_data).eq('lead_id', lead_id).execute()
                        print(f"✅ Updated existing client data for lead {lead_id}")
                    else:
                        print(f"📝 No existing record for lead {lead_id}, inserting new...")
                        save_result = supabase.table('clients_data').insert(save_data).execute()
                        print(f"✅ Inserted new client data for lead {lead_id}")
                        if save_result.data:
                            print(f"   Inserted ID: {save_result.data[0].get('id')}")
                else:
                    # Always upsert by email if no lead_id
                    result = supabase.table('clients_data').select('id').eq('email', email).limit(1).execute()
                    if result.data and len(result.data) > 0:
                        print(f"🔄 Existing record found for email {email}, updating...")
                        save_result = supabase.table('clients_data').update(save_data).eq('email', email).execute()
                        print(f"✅ Updated client data by email {email}")
                    else:
                        print(f"📝 No existing record for email {email}, inserting new...")
                        save_result = supabase.table('clients_data').insert(save_data).execute()
                        print(f"✅ Inserted new client data by email {email}")
                        if save_result.data:
                            print(f"   Inserted ID: {save_result.data[0].get('id')}")
            except Exception as e:
                print(f"❌ Error saving client data: {str(e)}")
                import traceback
                traceback.print_exc()
                save_result = None
        else:
            print(f"❌ Cannot save - no email available in drivers")
            return jsonify({
                'success': False,
                'error': 'Cannot save without email'
            }), 400
        
        print(f"✅ Client data save operation completed")
        
        # Verify data was actually saved
        try:
            verify_result = supabase.table('clients_data').select('count', count='exact').execute()
            print(f"📊 Total clients_data rows in DB: {verify_result.count}")
        except Exception as e:
            print(f"⚠️ Could not verify save: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': 'Client data saved successfully',
            'lead_id': lead_id,
            'email': email,
            'phone': phone
        }), 200
        
    except Exception as e:
        print(f"❌ Error saving client: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Failed to save client: {str(e)}'
        }), 500


@app.route('/api/get-client-data/<query>', methods=['GET'])
def get_client_data(query):
    """Retrieve saved client data by email or lead ID"""
    try:
        print(f"📂 Retrieving client data for: {query}")
        
        # Try to find by lead_id first (if valid UUID format)
        try:
            if len(query) == 36 and query.count('-') == 4:  # UUID format check
                result = supabase.table('clients_data').select('*').eq('lead_id', query).limit(1).execute()
                if result.data and len(result.data) > 0:
                    print(f"✅ Found client data by lead_id: {query}")
                    return jsonify({
                        'success': True,
                        'data': result.data[0]
                    }), 200
        except Exception as e:
            print(f"⚠️ Error searching by lead_id: {str(e)}")
        
        # Try to find by email (primary search)
        try:
            result = supabase.table('clients_data').select('*').eq('email', query).limit(1).execute()
            if result.data and len(result.data) > 0:
                print(f"✅ Found client data by email: {query}")
                return jsonify({
                    'success': True,
                    'data': result.data[0]
                }), 200
        except Exception as e:
            print(f"⚠️ Error searching by email: {str(e)}")
        
        print(f"⚠️ No client data found for: {query}")
        return jsonify({
            'success': False,
            'error': 'No data found',
            'data': None
        }), 404
        
    except Exception as e:
        print(f"❌ Error retrieving client data: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Failed to retrieve client data: {str(e)}'
        }), 500



@app.route('/api/get-property-data/<query>', methods=['GET'])
def get_property_data(query):
    """Retrieve saved property data by email or lead ID"""
    try:
        print(f"📂 Retrieving property data for: {query}")
        
        # Try to find by email (primary search)
        try:
            result = supabase.table('properties_data').select('*').eq('email', query).limit(1).execute()
            if result.data and len(result.data) > 0:
                print(f"✅ Found property data by email: {query}")
                return jsonify({
                    'success': True,
                    'data': result.data[0]
                }), 200
        except Exception as e:
            print(f"⚠️ Error searching by email: {str(e)}")
        
        print(f"⚠️ No property data found for: {query}")
        return jsonify({
            'success': False,
            'error': 'No data found',
            'data': None
        }), 404
        
    except Exception as e:
        print(f"❌ Error retrieving property data: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Failed to retrieve property data: {str(e)}'
        }), 500


@app.route('/api/get-auto-data/<query>', methods=['GET'])
def get_auto_data(query):
    """Retrieve saved auto dashboard data by email"""
    try:
        print(f"🚗 Retrieving auto data for: {query}")

        # Try to find by lead_id first (if valid UUID format)
        try:
            if len(query) == 36 and query.count('-') == 4:  # UUID format check
                result = supabase.table('auto_data').select('*').eq('lead_id', query).limit(1).execute()
                if result.data and len(result.data) > 0:
                    print(f"✅ Found auto data by lead_id: {query}")
                    return jsonify({
                        'success': True,
                        'data': result.data[0]
                    }), 200
        except Exception as e:
            print(f"⚠️ Error searching auto data by lead_id: {str(e)}")
        
        # Try to find by email
        try:
            result = supabase.table('auto_data').select('*').eq('email', query).limit(1).execute()
            if result.data and len(result.data) > 0:
                print(f"✅ Found auto data by email: {query}")
                return jsonify({
                    'success': True,
                    'data': result.data[0]
                }), 200
        except Exception as e:
            print(f"⚠️ Error searching auto data by email: {str(e)}")
        
        print(f"⚠️ No auto data found for: {query}")
        return jsonify({
            'success': False,
            'error': 'No data found',
            'data': None
        }), 404
        
    except Exception as e:
        print(f"❌ Error retrieving auto data: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Failed to retrieve auto data: {str(e)}'
        }), 500


@app.route('/api/save-property', methods=['POST'])
def save_property():
    """Save complete property data to Supabase linked to a lead"""
    try:
        data = request.json
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        print(f"🏠 Saving property data to Supabase...")
        print(f"📊 Data received keys: {list(data.keys())}")
        
        # Get email and leadId from request data
        email = None
        lead_id = None
        provided_lead_id = data.get('leadId')  # Frontend may provide leadId directly
        
        # Get email from request or from customer object
        if data.get('email'):
            email = data['email'].strip().lower()
        elif data.get('customer') and isinstance(data['customer'], dict):
            email = data['customer'].get('email', '').strip().lower() if data['customer'].get('email') else None
        
        if email:
            print(f"✓ Extracted email: {email}")
        
        if not email:
            print(f"⚠️ No email provided in request or customer data")
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        # Prefer leadId from frontend if provided, otherwise find lead by email
        if provided_lead_id and provided_lead_id != 'default':
            # Validate the provided leadId exists in leads table (check both 'id' and 'meta_lead_id')
            try:
                # First try matching by database id
                result = supabase.table('leads').select('id').eq('id', provided_lead_id).limit(1).execute()
                if result.data and len(result.data) > 0:
                    lead_id = result.data[0]['id']
                    print(f"✅ Using provided leadId (by id): {lead_id}")
                else:
                    # Try matching by meta_lead_id (Facebook lead ID)
                    result = supabase.table('leads').select('id').eq('meta_lead_id', provided_lead_id).limit(1).execute()
                    if result.data and len(result.data) > 0:
                        lead_id = result.data[0]['id']
                        print(f"✅ Using provided leadId (by meta_lead_id): {lead_id}")
                    else:
                        print(f"⚠️ Provided leadId not found in leads table, falling back to email lookup")
            except Exception as e:
                print(f"⚠️ Error validating leadId: {str(e)}, falling back to email lookup")
        
        # Fallback: Find lead by email if leadId wasn't provided or wasn't found
        if not lead_id and email:
            try:
                result = supabase.table('leads').select('id').eq('email', email).limit(1).execute()
                if result.data and len(result.data) > 0:
                    lead_id = result.data[0]['id']
                    print(f"✅ Found lead by email {email}: {lead_id}")
            except Exception as e:
                print(f"⚠️ Error finding lead by email: {str(e)}")
        
        # Prepare data for storage - WORKAROUND for PostgREST schema cache issue
        # Save all dual-mode data in the 'customer' JSONB column to avoid schema cache conflicts
        combined_data = {
            'viewMode': data.get('viewMode', 'Homeowners'),
            'homeowners': data.get('homeowners', {}),
            'tenants': data.get('tenants', {}),
        }
        
        # Use properties column for backwards compatibility, customer column for dual-mode data
        save_data = {
            'email': email,
            'properties': data.get('properties', []),  # For backwards compatibility
            'customer': combined_data,  # Store all dual-mode data here (JSONB works fine)
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Add lead_id if found
        if lead_id:
            save_data['lead_id'] = lead_id
        
        print(f"\n{'='*80}")
        print(f"💾 INSERT/UPDATE STEP - Saving property data for email: {email}")
        print(f"{'='*80}")
        print(f"📦 Data to save keys: {list(save_data.keys())}")
        print(f"\n📋 DETAILED PAYLOAD RECEIVED:")
        print(f"   viewMode: {combined_data.get('viewMode')}")
        print(f"   homeowners exists: {bool(data.get('homeowners'))}")
        if data.get('homeowners'):
            print(f"      - homeowners.customer keys: {list(data['homeowners'].get('customer', {}).keys())}")
            print(f"      - homeowners.properties count: {len(data['homeowners'].get('properties', []))}")
        print(f"   tenants exists: {bool(data.get('tenants'))}")
        if data.get('tenants'):
            print(f"      - tenants.customer keys: {list(data['tenants'].get('customer', {}).keys())}")
            print(f"      - tenants.properties count: {len(data['tenants'].get('properties', []))}")
        
        print(f"\n🏠 Saving Homeowners data: {bool(data.get('homeowners'))}")
        if data.get('homeowners'):
            print(f"   Homeowners customer: {json.dumps(data['homeowners'].get('customer', {}), indent=2)[:200]}...")
        print(f"🏢 Saving Tenants data: {bool(data.get('tenants'))}")
        if data.get('tenants'):
            print(f"   Tenants customer: {json.dumps(data['tenants'].get('customer', {}), indent=2)[:200]}...")
        
        # Insert or update in properties_data table - prefer lead_id matching when available
        if lead_id:
            print(f"💡 Saving property data by lead_id: {lead_id}")
            try:
                # Check if record exists by lead_id first
                result = supabase.table('properties_data').select('id').eq('lead_id', lead_id).limit(1).execute()
                if result.data and len(result.data) > 0:
                    print(f"🔄 Existing record found for lead_id {lead_id}, updating...")
                    save_result = supabase.table('properties_data').update(save_data).eq('lead_id', lead_id).execute()
                    print(f"✅ Updated existing property data for lead_id {lead_id}")
                else:
                    # No record by lead_id, check by email for backward compatibility
                    result = supabase.table('properties_data').select('id').eq('email', email).limit(1).execute()
                    if result.data and len(result.data) > 0:
                        print(f"🔄 Existing record found for email {email}, updating with lead_id...")
                        save_result = supabase.table('properties_data').update(save_data).eq('email', email).execute()
                        print(f"✅ Updated existing property data for email {email}")
                    else:
                        print(f"📝 No existing record, inserting new for lead_id {lead_id}...")
                        save_result = supabase.table('properties_data').insert(save_data).execute()
                        print(f"✅ Inserted new property data for lead_id {lead_id}")
                        if save_result.data:
                            print(f"   Inserted ID: {save_result.data[0].get('id')}")
            except Exception as e:
                print(f"❌ Error saving property data by lead_id: {str(e)}")
                import traceback
                traceback.print_exc()
                return jsonify({'success': False, 'error': f'Failed to save: {str(e)}'}), 500
        elif email:
            # Fallback to email-only matching for records without lead_id
            print(f"💡 Saving property data by email (fallback): {email}")
            try:
                # Check if record exists
                result = supabase.table('properties_data').select('id').eq('email', email).limit(1).execute()
                if result.data and len(result.data) > 0:
                    print(f"🔄 Existing record found for email {email}, updating...")
                    save_result = supabase.table('properties_data').update(save_data).eq('email', email).execute()
                    print(f"✅ Updated existing property data for email {email}")
                    
                    # Verify what was saved
                    verify_result = supabase.table('properties_data').select('*').eq('email', email).limit(1).execute()
                    if verify_result.data:
                        saved_record = verify_result.data[0]
                        saved_customer = saved_record.get('customer', {})
                        print(f"\n📋 VERIFICATION - What was actually saved to database:")
                        print(f"   Record ID: {saved_record.get('id')}")
                        print(f"   Stored in customer column:")
                        print(f"      - Has viewMode: {bool(saved_customer.get('viewMode'))}")
                        print(f"      - Has homeowners: {bool(saved_customer.get('homeowners'))}")
                        print(f"      - Has tenants: {bool(saved_customer.get('tenants'))}")
                        if saved_customer.get('tenants'):
                            print(f"      - Saved tenants data: {json.dumps(saved_customer.get('tenants', {}), indent=2)[:300]}...")
                else:
                    print(f"📝 No existing record for email {email}, inserting new...")
                    save_result = supabase.table('properties_data').insert(save_data).execute()
                    print(f"✅ Inserted new property data for email {email}")
                    if save_result.data:
                        print(f"   Inserted ID: {save_result.data[0].get('id')}")
                        # Verify what was saved
                        verify_result = supabase.table('properties_data').select('*').eq('email', email).limit(1).execute()
                        if verify_result.data:
                            saved_record = verify_result.data[0]
                            saved_customer = saved_record.get('customer', {})
                            print(f"\n📋 VERIFICATION - What was actually saved to database:")
                            print(f"   Record ID: {saved_record.get('id')}")
                            print(f"   Stored in customer column:")
                            print(f"      - Has viewMode: {bool(saved_customer.get('viewMode'))}")
                            print(f"      - Has homeowners: {bool(saved_customer.get('homeowners'))}")
                            print(f"      - Has tenants: {bool(saved_customer.get('tenants'))}")
                            if saved_customer.get('tenants'):
                                print(f"      - Saved tenants data: {json.dumps(saved_customer.get('tenants', {}), indent=2)[:300]}...")
            except Exception as e:
                print(f"❌ Error saving property data: {str(e)}")
                import traceback
                traceback.print_exc()
                return jsonify({'success': False, 'error': f'Failed to save: {str(e)}'}), 500
        else:
            print(f"❌ Cannot save - no email available")
            return jsonify({
                'success': False,
                'error': 'Cannot save without email'
            }), 400
        
        print(f"✅ Property data save operation completed")
        
        return jsonify({
            'success': True,
            'message': 'Property data saved successfully',
            'lead_id': lead_id,
            'email': email
        }), 200
        
    except Exception as e:
        print(f"❌ Error saving property: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Failed to save property: {str(e)}'
        }), 500


@app.route('/api/save-property-v2', methods=['POST'])
def save_property_v2():
    """Save property data from redesigned property page to Supabase"""
    try:
        data = request.json
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        print(f"\n🏠 [V2] Saving redesigned property data to Supabase...")
        print(f"📊 Data received keys: {list(data.keys())}")
        
        # Extract data from new format
        user_type = data.get('userType')  # 'homeowner' or 'tenant'
        primary_type = data.get('primaryType')  # 'house' or 'condo' (for homeowners)
        customer = data.get('customer', {})
        primary_property = data.get('primaryProperty', {})
        rental_properties = data.get('rentalProperties', [])
        
        print(f"   userType: {user_type}")
        print(f"   primaryType: {primary_type}")
        print(f"   customer keys: {list(customer.keys())}")
        print(f"   primaryProperty keys: {list(primary_property.keys())}")
        print(f"   rentalProperties count: {len(rental_properties)}")
        
        # Get email from customer data
        email = customer.get('email', '').strip().lower() if customer.get('email') else None
        
        if not email:
            print(f"⚠️ No email provided in customer data")
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        print(f"✓ Using email: {email}")
        
        # Find lead by email
        lead_id = None
        try:
            result = supabase.table('leads').select('id').eq('email', email).limit(1).execute()
            if result.data and len(result.data) > 0:
                lead_id = result.data[0]['id']
                print(f"✅ Found lead by email: {lead_id}")
        except Exception as e:
            print(f"⚠️ Error finding lead by email: {str(e)}")
        
        # Transform new format to database format
        # Store in 'customer' JSONB column for the redesigned structure
        redesigned_data = {
            'version': 'v2',  # Mark this as v2 format
            'userType': user_type,
            'primaryType': primary_type,
            'customer': customer,
            'primaryProperty': primary_property,
            'rentalProperties': rental_properties
        }
        
        # Prepare data for database storage
        save_data = {
            'email': email,
            'customer': redesigned_data,  # All redesigned data in customer JSONB column
            'properties': [primary_property] + rental_properties,  # For backwards compatibility
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Add lead_id if found
        if lead_id:
            save_data['lead_id'] = lead_id
        
        print(f"\n{'='*80}")
        print(f"💾 [V2] Saving redesigned property data for email: {email}")
        print(f"{'='*80}")
        print(f"📦 Customer name: {customer.get('insName', 'N/A')}")
        print(f"🏠 Primary property address: {primary_property.get('address', 'N/A')}")
        print(f"🏢 Rental properties: {len(rental_properties)}")
        for i, rental in enumerate(rental_properties, 1):
            print(f"   {i}. {rental.get('type', 'N/A')}: {rental.get('address', 'N/A')}")
        
        # Insert or update in properties_data table
        if lead_id:
            print(f"💡 Saving by lead_id: {lead_id}")
            try:
                # Check if record exists by lead_id
                result = supabase.table('properties_data').select('id').eq('lead_id', lead_id).limit(1).execute()
                if result.data and len(result.data) > 0:
                    print(f"🔄 Existing record found, updating...")
                    save_result = supabase.table('properties_data').update(save_data).eq('lead_id', lead_id).execute()
                    print(f"✅ Updated property data for lead_id {lead_id}")
                else:
                    # Fallback to email check
                    result = supabase.table('properties_data').select('id').eq('email', email).limit(1).execute()
                    if result.data and len(result.data) > 0:
                        print(f"🔄 Found by email, updating with lead_id...")
                        save_result = supabase.table('properties_data').update(save_data).eq('email', email).execute()
                        print(f"✅ Updated property data for email {email}")
                    else:
                        print(f"📝 No existing record, inserting new...")
                        save_result = supabase.table('properties_data').insert(save_data).execute()
                        print(f"✅ Inserted new property data")
                        if save_result.data:
                            print(f"   Inserted ID: {save_result.data[0].get('id')}")
                
                # Verify what was saved
                verify_result = supabase.table('properties_data').select('*').eq('lead_id', lead_id).limit(1).execute()
                if verify_result.data:
                    saved_record = verify_result.data[0]
                    saved_customer = saved_record.get('customer', {})
                    print(f"\n📋 VERIFICATION - Saved to database:")
                    print(f"   Record ID: {saved_record.get('id')}")
                    print(f"   Version: {saved_customer.get('version')}")
                    print(f"   User Type: {saved_customer.get('userType')}")
                    print(f"   Primary Type: {saved_customer.get('primaryType')}")
                    print(f"   Has customer data: {bool(saved_customer.get('customer'))}")
                    print(f"   Has primary property: {bool(saved_customer.get('primaryProperty'))}")
                    print(f"   Rental properties count: {len(saved_customer.get('rentalProperties', []))}")
                    
            except Exception as e:
                print(f"❌ Error saving property data: {str(e)}")
                import traceback
                traceback.print_exc()
                return jsonify({'success': False, 'error': f'Failed to save: {str(e)}'}), 500
        elif email:
            # Fallback to email-only matching
            print(f"💡 Saving by email (no lead_id): {email}")
            try:
                result = supabase.table('properties_data').select('id').eq('email', email).limit(1).execute()
                if result.data and len(result.data) > 0:
                    print(f"🔄 Existing record found, updating...")
                    save_result = supabase.table('properties_data').update(save_data).eq('email', email).execute()
                    print(f"✅ Updated property data for email {email}")
                else:
                    print(f"📝 No existing record, inserting new...")
                    save_result = supabase.table('properties_data').insert(save_data).execute()
                    print(f"✅ Inserted new property data")
                    if save_result.data:
                        print(f"   Inserted ID: {save_result.data[0].get('id')}")
                
                # Verify what was saved
                verify_result = supabase.table('properties_data').select('*').eq('email', email).limit(1).execute()
                if verify_result.data:
                    saved_record = verify_result.data[0]
                    saved_customer = saved_record.get('customer', {})
                    print(f"\n📋 VERIFICATION - Saved to database:")
                    print(f"   Record ID: {saved_record.get('id')}")
                    print(f"   Version: {saved_customer.get('version')}")
                    print(f"   User Type: {saved_customer.get('userType')}")
                    print(f"   Has customer data: {bool(saved_customer.get('customer'))}")
                    print(f"   Rental properties: {len(saved_customer.get('rentalProperties', []))}")
                    
            except Exception as e:
                print(f"❌ Error saving property data: {str(e)}")
                import traceback
                traceback.print_exc()
                return jsonify({'success': False, 'error': f'Failed to save: {str(e)}'}), 500
        else:
            print(f"❌ Cannot save - no email available")
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        print(f"✅ [V2] Property data save operation completed")
        
        return jsonify({
            'success': True,
            'message': 'Property data saved successfully',
            'lead_id': lead_id,
            'email': email,
            'version': 'v2'
        }), 200
        
    except Exception as e:
        print(f"❌ [V2] Error saving property: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Failed to save property: {str(e)}'
        }), 500


@app.route('/api/save-auto-data', methods=['POST'])
def save_auto_data():
    """Save auto dashboard data to Supabase linked to a lead"""
    try:
        data = request.json
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        print(f"🚗 Saving auto dashboard data to Supabase...")
        print(f"📊 Data received keys: {list(data.keys())}")
        
        # Get email and leadId from request data
        email = None
        lead_id = None
        provided_lead_id = data.get('leadId')  # Frontend may provide leadId directly
        
        if data.get('email'):
            email = data['email'].strip().lower()
            print(f"✓ Extracted email: {email}")
        
        if not email:
            print(f"⚠️ No email provided")
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        # Prefer leadId from frontend if provided, otherwise find lead by email
        if provided_lead_id and provided_lead_id != 'default':
            # Validate the provided leadId exists in leads table
            try:
                result = supabase.table('leads').select('id').eq('id', provided_lead_id).limit(1).execute()
                if result.data and len(result.data) > 0:
                    lead_id = result.data[0]['id']
                    print(f"✅ Using provided leadId: {lead_id}")
                else:
                    print(f"⚠️ Provided leadId not found in leads table, falling back to email lookup")
            except Exception as e:
                print(f"⚠️ Error validating leadId: {str(e)}, falling back to email lookup")
        
        # Fallback: Find lead by email if leadId wasn't provided or wasn't found
        if not lead_id and email:
            try:
                result = supabase.table('leads').select('id').eq('email', email).limit(1).execute()
                if result.data and len(result.data) > 0:
                    lead_id = result.data[0]['id']
                    print(f"✅ Found lead by email {email}: {lead_id}")
            except Exception as e:
                print(f"⚠️ Error finding lead by email: {str(e)}")
        
        # Prepare data for storage
        save_data = {
            'email': email,
            'auto_data': data.get('auto_data', {}),
            'customer': data.get('customer', {}),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Add lead_id if found
        if lead_id:
            save_data['lead_id'] = lead_id
        
        print(f"💾 INSERT/UPDATE STEP - lead_id: {lead_id}, email: {email}")
        print(f"📦 Data to save keys: {list(save_data.keys())}")
        
        # Insert or update in auto_data table - prefer lead_id matching when available
        if lead_id:
            print(f"💡 Saving auto data by lead_id: {lead_id}")
            try:
                # Check if record exists by lead_id first
                result = supabase.table('auto_data').select('id').eq('lead_id', lead_id).limit(1).execute()
                if result.data and len(result.data) > 0:
                    print(f"🔄 Existing record found for lead_id {lead_id}, updating...")
                    save_result = supabase.table('auto_data').update(save_data).eq('lead_id', lead_id).execute()
                else:
                    # No record by lead_id, check by email for backward compatibility
                    result = supabase.table('auto_data').select('id').eq('email', email).limit(1).execute()
                    if result.data and len(result.data) > 0:
                        print(f"🔄 Existing record found for email {email}, updating with lead_id...")
                        save_result = supabase.table('auto_data').update(save_data).eq('email', email).execute()
                    else:
                        print(f"📝 No existing record, inserting new for lead_id {lead_id}...")
                        save_result = supabase.table('auto_data').insert(save_data).execute()
                        print(f"✅ Inserted new auto data for lead_id {lead_id}")
                        if save_result.data:
                            print(f"   Inserted ID: {save_result.data[0].get('id')}")
                return jsonify({
                    'success': True,
                    'message': 'Auto data saved successfully',
                    'lead_id': lead_id,
                    'email': email
                }), 200
            except Exception as e:
                print(f"❌ Error saving by lead_id: {str(e)}")
                import traceback
                traceback.print_exc()
                return jsonify({'success': False, 'error': f'Failed to save: {str(e)}'}), 500
        elif email:
            # Fallback to email-only matching for records without lead_id
            print(f"💡 Saving auto data by email (fallback): {email}")
            try:
                # Check if record exists
                result = supabase.table('auto_data').select('id').eq('email', email).limit(1).execute()
                if result.data and len(result.data) > 0:
                    print(f"🔄 Existing record found for email {email}, updating...")
                    save_result = supabase.table('auto_data').update(save_data).eq('email', email).execute()
                    print(f"✅ Updated existing auto data for email {email}")
                else:
                    print(f"📝 No existing record for email {email}, inserting new...")
                    save_result = supabase.table('auto_data').insert(save_data).execute()
                    print(f"✅ Inserted new auto data for email {email}")
                    if save_result.data:
                        print(f"   Inserted ID: {save_result.data[0].get('id')}")
            except Exception as e:
                print(f"❌ Error saving auto data: {str(e)}")
                import traceback
                traceback.print_exc()
                return jsonify({'success': False, 'error': f'Failed to save: {str(e)}'}), 500
        else:
            print(f"❌ Cannot save - no email available")
            return jsonify({
                'success': False,
                'error': 'Cannot save without email'
            }), 400
        
        print(f"✅ Auto data save operation completed")
        
        return jsonify({
            'success': True,
            'message': 'Auto data saved successfully',
            'lead_id': lead_id,
            'email': email
        }), 200
        
    except Exception as e:
        print(f"❌ Error saving auto data: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Failed to save auto data: {str(e)}'
        }), 500


# ========== CLIENT DOCUMENT MANAGEMENT ==========

@app.route('/api/client-documents/upload', methods=['POST'])
def upload_client_document():
    """Upload pre-client documents to client folder with signing/non-signing classification"""
    try:
        # Support both single 'file' and multiple 'files[]'
        files = []
        if 'file' in request.files:
            files = [request.files['file']]
        elif 'files[]' in request.files:
            files = request.files.getlist('files[]')
        
        if not files:
            return jsonify({'error': 'No files provided'}), 400
        
        lead_id = request.form.get('lead_id')  # Optional
        client_name = request.form.get('client_name')
        category = request.form.get('category')  # 'auto' or 'home'
        document_type = request.form.get('document_type', 'nonsigning')  # 'signing' or 'nonsigning'
        
        if not client_name or not category:
            return jsonify({'error': 'client_name and category are required'}), 400
        
        if category not in ['auto', 'home']:
            return jsonify({'error': 'category must be "auto" or "home"'}), 400
        
        if document_type not in ['signing', 'nonsigning']:
            return jsonify({'error': 'document_type must be "signing" or "nonsigning"'}), 400
        
        # Auto-resolve lead_id from client_name if not provided
        if not lead_id:
            try:
                lead_res = supabase.table('leads').select('id').eq('name', client_name).limit(1).execute()
                if lead_res.data:
                    lead_id = lead_res.data[0]['id']
                    print(f"  Auto-resolved lead_id={lead_id} from name={client_name}")
            except Exception:
                pass
        
        if not lead_id:
            return jsonify({'error': 'lead_id is required (provide it or ensure a lead with matching client_name exists)'}), 400
        
        uploaded_files = []
        errors = []
        
        for file in files:
            if file.filename == '':
                continue
            
            try:
                # Sanitize filename
                original_filename = secure_filename(file.filename)
                
                # Generate storage path: signed-documents/{client_name}/{category}/{document_type}/{filename}
                storage_path = f"{client_name}/{category}/{document_type}/{original_filename}"
                
                # Read file content
                file_content = file.read()
                file_size = len(file_content)
                mime_type = file.content_type or 'application/octet-stream'
                
                # Upload to Supabase storage (upsert=true overwrites existing file)
                try:
                    result = supabase.storage.from_('signed-documents').upload(
                        storage_path,
                        file_content,
                        {'content-type': mime_type, 'upsert': 'true'}
                    )
                except Exception as storage_err:
                    raise Exception(f"Storage upload failed: {storage_err}")
                
                # Save metadata to database (delete old row if exists, then insert)
                doc_data = {
                    'client_name': client_name,
                    'category': category,
                    'document_type': document_type,
                    'document_name': os.path.splitext(original_filename)[0],
                    'original_filename': original_filename,
                    'storage_path': storage_path,
                    'file_size': file_size,
                    'mime_type': mime_type,
                    'uploaded_by': 'system'
                }
                # Only include lead_id if provided (column may have NOT NULL constraint)
                if lead_id:
                    doc_data['lead_id'] = lead_id
                
                # Remove existing row for this path (idempotent re-upload)
                try:
                    supabase.table('client_documents').delete().eq('storage_path', storage_path).execute()
                except Exception:
                    pass
                db_result = supabase.table('client_documents').insert(doc_data).execute()
                
                uploaded_files.append({
                    'filename': original_filename,
                    'storage_path': storage_path,
                    'size': file_size,
                    'document_type': document_type,
                    'id': db_result.data[0]['id']
                })
                
            except Exception as e:
                errors.append({
                    'filename': file.filename,
                    'error': str(e)
                })
        
        response = {
            'success': True,
            'status': 'success',
            'uploaded': len(uploaded_files),
            'files': uploaded_files
        }
        
        if errors:
            response['errors'] = errors
            # If all files failed, surface the first error message clearly
            if not uploaded_files:
                response['success'] = False
                response['error'] = errors[0].get('error', 'Upload failed') if errors else 'Upload failed'
        
        return jsonify(response)
    
    except Exception as e:
        print(f"❌ Error uploading client documents: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/client-documents/<lead_id>', methods=['GET'])
def get_client_documents(lead_id):
    """Get all pre-uploaded documents for a lead"""
    try:
        category = request.args.get('category')  # Optional filter
        
        query = supabase.table('client_documents').select('*').eq('lead_id', str(lead_id))
        
        if category:
            query = query.eq('category', category)
        
        result = query.order('uploaded_at', desc=True).execute()
        
        return jsonify({
            'status': 'success',
            'count': len(result.data),
            'documents': result.data
        })
    
    except Exception as e:
        print(f"❌ Error getting client documents: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/client-documents/signing', methods=['GET'])
def get_signing_documents():
    """Get signing documents ready to be sent for signing"""
    try:
        client_name = request.args.get('client_name')
        category = request.args.get('category')  # 'auto' or 'home'
        lead_id = request.args.get('lead_id')  # Optional
        
        if not client_name:
            return jsonify({'error': 'client_name is required'}), 400
        
        # Query signing documents
        query = supabase.table('client_documents')\
            .select('*')\
            .eq('client_name', client_name)\
            .eq('document_type', 'signing')
        
        if category:
            query = query.eq('category', category)
        
        if lead_id:
            query = query.eq('lead_id', lead_id)
        
        result = query.order('uploaded_at', desc=True).execute()
        
        # Also get download URLs for each document
        documents = []
        for doc in result.data:
            doc_with_url = doc.copy()
            try:
                # Generate signed URL for download
                signed_url = supabase.storage.from_('signed-documents').create_signed_url(
                    doc['storage_path'], 3600  # 1 hour expiry
                )
                doc_with_url['download_url'] = signed_url.get('signedURL', '')
            except:
                doc_with_url['download_url'] = ''
            documents.append(doc_with_url)
        
        return jsonify({
            'status': 'success',
            'count': len(documents),
            'documents': documents
        })
    
    except Exception as e:
        print(f"❌ Error getting signing documents: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/client-documents/by-client', methods=['GET'])
def get_client_documents_by_name():
    """Get all pre-uploaded documents by client name"""
    try:
        client_name = request.args.get('client_name')
        category = request.args.get('category')  # Optional filter
        
        if not client_name:
            return jsonify({'error': 'client_name is required'}), 400
        
        query = supabase.table('client_documents').select('*').eq('client_name', client_name)
        
        if category:
            query = query.eq('category', category)
        
        result = query.order('uploaded_at', desc=True).execute()
        
        return jsonify({
            'status': 'success',
            'count': len(result.data),
            'documents': result.data
        })
    
    except Exception as e:
        print(f"❌ Error getting client documents by name: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/client-documents/<doc_id>', methods=['DELETE'])
def delete_client_document(doc_id):
    """Delete a pre-uploaded document"""
    try:
        # Get document info
        doc_result = supabase.table('client_documents').select('storage_path').eq('id', doc_id).execute()
        
        if not doc_result.data:
            return jsonify({'error': 'Document not found'}), 404
        
        storage_path = doc_result.data[0]['storage_path']
        
        # Delete from storage
        try:
            supabase.storage.from_('signed-documents').remove([storage_path])
        except Exception as e:
            print(f"⚠️ Warning: Could not delete file from storage: {str(e)}")
        
        # Delete from database
        supabase.table('client_documents').delete().eq('id', doc_id).execute()
        
        return jsonify({
            'status': 'success',
            'message': 'Document deleted'
        })
    
    except Exception as e:
        print(f"❌ Error deleting client document: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/client-documents/<doc_id>/replace', methods=['POST'])
def replace_client_document(doc_id):
    """Replace the file content of an existing document, keeping the same storage path."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        new_file = request.files['file']
        if not new_file.filename:
            return jsonify({'error': 'No file selected'}), 400

        doc_res = supabase.table('client_documents').select('*').eq('id', doc_id).execute()
        if not doc_res.data:
            return jsonify({'error': 'Document not found'}), 404

        doc = doc_res.data[0]
        storage_path = doc['storage_path']
        mime_type = new_file.content_type or doc.get('mime_type', 'application/pdf')
        file_content = new_file.read()
        file_size = len(file_content)

        try:
            supabase.storage.from_('signed-documents').upload(
                storage_path, file_content,
                {'content-type': mime_type, 'x-upsert': 'true'}
            )
        except Exception as se:
            return jsonify({'error': f'Storage upload failed: {se}'}), 500

        try:
            supabase.table('client_documents').update({
                'file_size': file_size,
                'mime_type': mime_type,
            }).eq('id', doc_id).execute()
        except Exception as dbe:
            print(f"⚠️ DB update after replace failed: {dbe}")

        print(f"✅ Replaced doc {doc_id}: {storage_path} ({file_size} bytes)")
        return jsonify({
            'status': 'success',
            'message': 'Document replaced',
            'storage_path': storage_path,
            'size': file_size
        })

    except Exception as e:
        print(f"❌ Error replacing client document: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/client-documents/clear-all', methods=['POST'])
def clear_all_client_documents():
    """Delete ALL client documents from storage and database — one-time cleanup utility"""
    try:
        all_docs = supabase.table('client_documents').select('id,storage_path').execute()
        paths = [d['storage_path'] for d in all_docs.data if d.get('storage_path')]
        deleted_storage = 0
        failed_storage = 0
        if paths:
            for i in range(0, len(paths), 100):  # batch of 100
                batch = paths[i:i+100]
                try:
                    supabase.storage.from_('signed-documents').remove(batch)
                    deleted_storage += len(batch)
                except Exception as se:
                    print(f"⚠️ Storage batch delete error: {se}")
                    failed_storage += len(batch)
        # Delete all DB rows
        if all_docs.data:
            supabase.table('client_documents').delete()\
                .neq('id', '00000000-0000-0000-0000-000000000000').execute()
        print(f"✅ Cleared all docs: {len(all_docs.data)} DB rows, {deleted_storage} storage files")
        return jsonify({
            'success': True,
            'db_cleared': len(all_docs.data),
            'storage_deleted': deleted_storage,
            'storage_failed': failed_storage
        })
    except Exception as e:
        print(f"❌ Error clearing all documents: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/client-documents/download/<doc_id>', methods=['GET'])
def download_client_document(doc_id):
    """Download a pre-uploaded document"""
    try:
        # Get document info
        doc_result = supabase.table('client_documents').select('*').eq('id', doc_id).execute()
        
        if not doc_result.data:
            return jsonify({'error': 'Document not found'}), 404
        
        doc = doc_result.data[0]
        storage_path = doc['storage_path']
        
        # Download from storage
        file_data = supabase.storage.from_('signed-documents').download(storage_path)
        
        # Return file
        return send_file(
            BytesIO(file_data),
            mimetype=doc.get('mime_type', 'application/octet-stream'),
            as_attachment=True,
            download_name=doc['original_filename']
        )
    
    except Exception as e:
        print(f"❌ Error downloading client document: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/client-documents/view/<doc_id>', methods=['GET'])
def view_client_document(doc_id):
    """Serve a pre-uploaded client document inline for browser preview"""
    try:
        doc_result = supabase.table('client_documents').select('*').eq('id', doc_id).execute()
        if not doc_result.data:
            return jsonify({'error': 'Document not found'}), 404
        doc = doc_result.data[0]
        storage_path = doc['storage_path']
        file_data = supabase.storage.from_('signed-documents').download(storage_path)
        mime_type = doc.get('mime_type', 'application/pdf')
        from flask import Response as FlaskResponse
        return FlaskResponse(
            file_data,
            mimetype=mime_type,
            headers={
                'Content-Disposition': f'inline; filename="{doc["original_filename"]}"',
                'Content-Type': mime_type,
                'Cache-Control': 'no-store'
            }
        )
    except Exception as e:
        print(f"❌ Error viewing client document: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ========== FILE UPLOAD CONFIG ==========

import uuid
from werkzeug.utils import secure_filename

# Configure upload folder
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ========== VERTEX AI DOCUMENT VERIFICATION ENDPOINTS ==========

from vertex_ai_checker import get_document_verification_service

@app.route('/api/verify-documents', methods=['POST'])
def verify_documents():
    """
    Verify a package of documents using Vertex AI Gemini
    
    Request body:
    {
        "document_paths": ["path1.pdf", "path2.pdf", ...],
        "client_name": "optional client name",
        "policy_type": "auto | home | both"
    }
    
    OR upload files directly:
    - files: multiple PDF files
    - client_name: optional
    - policy_type: optional
    """
    try:
        doc_service = get_document_verification_service()
        
        # Check if files were uploaded or paths provided
        if 'files' in request.files:
            # Handle file uploads
            files = request.files.getlist('files')
            client_name = request.form.get('client_name')
            policy_type = request.form.get('policy_type')
            
            # Save uploaded files temporarily and track original filenames
            temp_paths = []
            original_filenames = []
            for file in files:
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    temp_path = os.path.join(UPLOAD_FOLDER, f"verify_{uuid.uuid4().hex}_{filename}")
                    file.save(temp_path)
                    temp_paths.append(temp_path)
                    original_filenames.append(filename)  # Keep original filename
                    print(f"📁 Saved temp file: {filename}")
            
            if not temp_paths:
                return jsonify({'error': 'No valid PDF files uploaded'}), 400
            
            # Run verification with original filenames
            result = doc_service.verify_document_package(
                pdf_files=temp_paths,
                client_name=client_name,
                policy_type=policy_type,
                document_names=original_filenames  # Pass the actual filenames
            )
            
            # Clean up temp files
            for path in temp_paths:
                try:
                    os.remove(path)
                except:
                    pass
            
            return jsonify(result)
        
        else:
            # Handle JSON request with paths
            data = request.json
            document_paths = data.get('document_paths', [])
            client_name = data.get('client_name')
            policy_type = data.get('policy_type')
            
            if not document_paths:
                return jsonify({'error': 'No document_paths provided'}), 400
            
            # Verify paths exist
            valid_paths = []
            for path in document_paths:
                if os.path.exists(path):
                    valid_paths.append(path)
                else:
                    print(f"⚠️ File not found: {path}")
            
            if not valid_paths:
                return jsonify({'error': 'No valid document paths found'}), 400
            
            result = doc_service.verify_document_package(
                pdf_files=valid_paths,
                client_name=client_name,
                policy_type=policy_type
            )
            
            return jsonify(result)
    
    except Exception as e:
        print(f"❌ Error in verify_documents: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'overall_status': 'ERROR'}), 500


@app.route('/api/quick-signature-check', methods=['POST'])
def quick_signature_check():
    """
    Quick signature presence check for a single document
    
    Request: Upload a single PDF file
    Response: JSON with signature analysis
    """
    try:
        doc_service = get_document_verification_service()
        
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        
        if not file or not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Must be PDF'}), 400
        
        # Save temporarily
        filename = secure_filename(file.filename)
        temp_path = os.path.join(UPLOAD_FOLDER, f"sigcheck_{uuid.uuid4().hex}_{filename}")
        file.save(temp_path)
        
        try:
            result = doc_service.quick_signature_check(temp_path)
            return jsonify(result)
        finally:
            # Clean up
            try:
                os.remove(temp_path)
            except:
                pass
    
    except Exception as e:
        print(f"❌ Error in quick_signature_check: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ========== INITIALIZATION ==========

# ========== SIGNWELL INTEGRATION ==========

from signwell_service import SignWellService

# Initialize SignWell service
signwell_service = SignWellService(supabase)

@app.route('/api/signwell/test', methods=['GET'])
def signwell_test_connection():
    """Test SignWell API connection"""
    if not ENABLE_DEBUG_ENDPOINTS:
        return jsonify({'error': 'Not found'}), 404
    try:
        result = signwell_service.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/signwell/send', methods=['POST'])
def signwell_send_single():
    """Send a single document for signing via SignWell"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '' or not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file'}), 400
        
        document_name = request.form.get('document_name', file.filename)
        recipient_email = request.form.get('recipient_email')
        recipient_name = request.form.get('recipient_name')
        client_name = request.form.get('client_name')
        lead_id = request.form.get('lead_id')
        category = request.form.get('category')
        message = request.form.get('message', '')
        
        if not recipient_email:
            return jsonify({'error': 'Recipient email is required'}), 400
        if not recipient_name:
            return jsonify({'error': 'Recipient name is required'}), 400
        if not client_name:
            return jsonify({'error': 'Client name is required'}), 400
        
        # Save file temporarily
        filename = secure_filename(file.filename)
        file_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4()}_{filename}")
        file.save(file_path)
        
        try:
            result = signwell_service.send_documents(
                file_paths=[file_path],
                signer_name=recipient_name,
                signer_email=recipient_email,
                document_name=document_name,
                message=message,
            )
            return jsonify({'status': 'success', 'data': result})
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
    
    except Exception as e:
        print(f"❌ SignWell send error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/send-for-signing', methods=['POST'])
def signwell_send_portal_docs():
    """
    Send documents from portal via SignWell with token-prefix manifest tracking.
    Each file gets a unique 8-char token embedded in its filename before sending.
    Token travels through SignWell so we can reliably match signed files back to
    their original category (auto/home) on completion — even if filenames are
    otherwise identical across clients.
    """
    import re as _re
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        client_name   = data.get('client_name')
        lead_id       = data.get('lead_id')
        documents     = data.get('documents', [])
        signers       = data.get('signers', [])
        fields        = data.get('fields', [])
        request_name  = data.get('request_name', 'Signing Request')
        message       = data.get('message', '')
        test_mode     = data.get('test_mode', False)

        if not client_name:
            return jsonify({'error': 'Client name is required'}), 400
        if not documents:
            return jsonify({'error': 'No documents provided'}), 400
        if not signers:
            return jsonify({'error': 'At least one signer is required'}), 400

        print(f"📤 SignWell: Sending {len(documents)} docs for {client_name}, {len(fields)} fields placed")

        # ── BUILD TOKEN MANIFEST ──────────────────────────────────────
        # Format: kmi{8hextoken}_{original_filename}
        # Token is stored in DB so we can match signed files on return.
        # Primary match = parse token from returned filename.
        # Fallback match = positional order in manifest.
        TOKEN_RE = _re.compile(r'^kmi([a-f0-9]{8})_(.+)$', _re.IGNORECASE)
        files_manifest = []
        temp_files = []

        try:
            for idx, doc in enumerate(documents):
                file_path = doc.get('storage_path') or doc.get('file_path')
                if not file_path:
                    continue

                # Generate unique token
                token = uuid.uuid4().hex[:8]
                original_filename = doc.get('original_filename', file_path.split('/')[-1])
                safe_orig = secure_filename(original_filename)
                tokenized_filename = f"kmi{token}_{safe_orig}"

                # Download from Supabase storage
                response_data = supabase.storage.from_('signed-documents').download(file_path)
                if not response_data:
                    print(f"  ⚠️ Could not download: {file_path}")
                    continue

                temp_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4()}_{tokenized_filename}")
                with open(temp_path, 'wb') as f:
                    f.write(response_data)
                temp_files.append({'path': temp_path, 'tokenized_filename': tokenized_filename})

                files_manifest.append({
                    'index':              idx,
                    'token':              token,
                    'category':           doc.get('category', 'auto'),
                    'doc_type':           doc.get('document_type', 'signing'),
                    'original_filename':  original_filename,
                    'tokenized_filename': tokenized_filename,
                    'client_doc_id':      str(doc.get('id', '')),
                    'storage_path':       file_path,
                })
                print(f"  ✓ Tokenized [{doc.get('category','?')}] {original_filename} → {tokenized_filename}")

                # ── ORGANIZE REFERENCE DOCUMENTS ──────────────────────────
                # Copy nonsigning reference documents to organized folder structure
                # so they're available alongside signed docs for email sending
                if doc.get('document_type') == 'nonsigning':
                    try:
                        safe_client = (client_name or 'unknown').replace('/', '_').replace(' ', '_')
                        category = doc.get('category', 'auto').lower()
                        organized_path = f"{safe_client}/{category}/reference/{original_filename}"
                        
                        # Copy to organized location in signed-documents bucket (only bucket)
                        supabase.storage.from_('signed-documents').upload(
                            organized_path, response_data,
                            {'content-type': 'application/pdf', 'x-upsert': 'true'}
                        )
                        
                        # Delete existing row for this path then insert (no UNIQUE constraint on storage_path)
                        try:
                            supabase.table('client_documents').delete().eq('storage_path', organized_path).execute()
                        except Exception:
                            pass
                        supabase.table('client_documents').insert({
                            'lead_id':           lead_id,
                            'client_name':       client_name,
                            'category':          category,
                            'document_type':     'nonsigning',
                            'document_name':     original_filename,
                            'original_filename': original_filename,
                            'storage_path':      organized_path,
                            'file_size':         len(response_data),
                            'mime_type':         'application/pdf',
                            'uploaded_by':       'signwell_send',
                        }).execute()
                        
                        print(f"    ✓ Organized reference doc: {organized_path}")
                    except Exception as org_err:
                        print(f"    ⚠️ Failed to organize reference doc: {org_err}")

            if not temp_files:
                return jsonify({'error': 'Could not download any documents'}), 400

            file_paths = [f['path'] for f in temp_files]

            result = signwell_service.send_with_fields(
                file_paths=file_paths,
                signers=signers,
                fields=fields,
                document_name=request_name,
                message=message,
                test_mode=test_mode,
            )

            if not result.get('success'):
                return jsonify({'error': result.get('error', 'SignWell send failed')}), 500

            doc_id = result.get('document_id')

            # ── STORE SEND LOG (primary tracking table) ──────────────
            if doc_id:
                try:
                    supabase.table('signwell_send_log').insert({
                        'signwell_document_id': doc_id,
                        'lead_id':              lead_id,
                        'client_name':          client_name,
                        'status':               'pending',
                        'files_manifest':       files_manifest,
                    }).execute()
                    print(f"✅ Send log stored: {doc_id[:16]}… ({len(files_manifest)} files)")
                except Exception as log_err:
                    print(f"⚠️ Send log storage failed (run SQL setup): {log_err}")

                # Also update legacy table for backward compatibility
                try:
                    primary_signer = signers[0] if signers else {}
                    supabase.table('zoho_sign_requests').upsert({
                        'request_id':      doc_id,
                        'document_name':   request_name,
                        'recipient_name':  primary_signer.get('name', ''),
                        'recipient_email': primary_signer.get('email', ''),
                        'lead_id':         lead_id,
                        'client_name':     client_name,
                        'category':        'mixed',
                        'bucket_folder':   f"{client_name.replace(' ','_')}/mixed",
                        'status':          'pending',
                    }, on_conflict='request_id').execute()
                except Exception as legacy_err:
                    print(f"⚠️ Legacy table update failed: {legacy_err}")

            return jsonify({
                'status':           'success',
                'document_id':       doc_id,
                'manifest_stored':   len(files_manifest),
                'message':          f'Documents sent — {len(files_manifest)} files tracked with tokens',
                'data':              result,
            })

        finally:
            for f in temp_files:
                try:
                    os.remove(f['path'])
                except Exception:
                    pass

    except Exception as e:
        print(f"❌ SignWell portal send error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# SIGNWELL POST-SIGN INFRASTRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

def _build_bundles_for_client(client_name, lead_id):
    """Merge signed + reference PDFs into per-category bundles after signing completes."""
    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfWriter, PdfReader
        except ImportError:
            print("⚠️ No PDF merge library (pip install pypdf)"); return

    safe_cn = (client_name or 'unknown').replace('/', '_').replace(' ', '_')

    for category in ['auto', 'home']:
        try:
            signed_docs = supabase.table('client_documents').select('*')\
                .eq('client_name', client_name).eq('category', category)\
                .eq('document_type', 'signed').execute().data

            ref_docs = supabase.table('client_documents').select('*')\
                .eq('client_name', client_name).eq('category', category)\
                .eq('document_type', 'nonsigning').execute().data

            all_docs = signed_docs + ref_docs
            if not all_docs:
                print(f"  📭 No docs for {category} bundle"); continue

            import io as _io
            writer = PdfWriter()
            bundle_pages = 0

            for doc in all_docs:
                try:
                    pdf_bytes = supabase.storage.from_('signed-documents').download(doc['storage_path'])
                    reader = PdfReader(_io.BytesIO(pdf_bytes))
                    for page in reader.pages:
                        writer.add_page(page)
                    bundle_pages += len(reader.pages)
                except Exception as pg_err:
                    print(f"    ⚠️ Skipping {doc.get('original_filename')}: {pg_err}")

            if bundle_pages == 0:
                continue

            buf = _io.BytesIO()
            writer.write(buf)
            bundle_path = f"{safe_cn}/{category}/bundles/{safe_cn}_{category.upper()}_bundle.pdf"

            supabase.storage.from_('signed-documents').upload(
                bundle_path, buf.getvalue(),
                {'content-type': 'application/pdf', 'x-upsert': 'true'}
            )
            try:
                supabase.table('signwell_send_log')\
                    .update({f'bundle_{category}_path': bundle_path})\
                    .eq('client_name', client_name).execute()
            except Exception:
                pass
            print(f"  ✅ {category.upper()} bundle: {bundle_pages}pp → {bundle_path}")

        except Exception as cat_err:
            print(f"  ⚠️ Bundle error [{category}]: {cat_err}")


# ─────────────────────────────────────────────────────────────────────────────
# SHARED COMPLETION HANDLER  (used by both webhook and sync-status)
# ─────────────────────────────────────────────────────────────────────────────

def _handle_manifest_completion(doc_id):
    """
    Process a completed SignWell document using the signwell_send_log manifest.
    Downloads the combined signed PDF, splits it by per-file page counts,
    matches each segment to its original category via token, and stores the
    signed segments under the correct category.

    Returns: (success: bool, processed_filenames: list[str])
    """
    import re as _re
    import io as _io
    TOKEN_RE = _re.compile(r'kmi([a-f0-9]{8})_', _re.IGNORECASE)

    # ── LOOKUP MANIFEST ──────────────────────────────────────────────
    log_res = supabase.table('signwell_send_log')\
        .select('*').eq('signwell_document_id', doc_id).limit(1).execute()
    if not log_res.data:
        print(f"⚠️ No send log found for {doc_id} — cannot use manifest path")
        return False, []

    log = log_res.data[0]
    files_manifest = log.get('files_manifest', [])
    client_name    = log.get('client_name', '')
    lead_id        = log.get('lead_id', '')
    safe_cn        = (client_name or 'unknown').replace('/', '_').replace(' ', '_')

    print(f"📋 Manifest completion for {doc_id[:16]}: {len(files_manifest)} files, client={client_name}")

    # ── GET FILE METADATA (names + page counts) ─────────────────────
    meta = signwell_service.get_completed_documents(doc_id)
    if not meta.get('success'):
        print(f"⚠️ Could not get doc metadata: {meta.get('error')}")
        return False, []

    sw_files = meta.get('files', [])
    print(f"  SignWell files ({len(sw_files)}): " + ", ".join(
        f"{f['name'][:30]}({f['pages_number']}pp)" for f in sw_files))

    # ── MATCH SW FILES → MANIFEST via token in filename ──────────────
    # Build a map: sw_file_index → (manifest_entry, sw_file)
    file_map = {}  # {index: (entry, sf)} — preserves order, handles gaps
    for idx, sf in enumerate(sw_files):
        sf_name = sf.get('name', '')
        m = TOKEN_RE.search(sf_name)
        entry = None
        if m:
            token = m.group(1)
            entry = next((e for e in files_manifest if e.get('token') == token), None)
        # Fallback: positional (only for unmatched non-signature files)
        fallback_idx = len([v for v in file_map.values() if v[0] is not None])
        if not entry and fallback_idx < len(files_manifest) and 'signature' not in sf_name.lower():
            entry = files_manifest[fallback_idx]
        if entry:
            file_map[idx] = (entry, sf)
            print(f"  ✓ Matched: {sf_name[:40]} → [{entry.get('category')}] {entry.get('original_filename')}")
        else:
            # Signature page or unrecognized file — track it but no manifest entry
            file_map[idx] = (None, sf)
            print(f"  ⚠️ Unmatched file (skipping): {sf_name}")

    matched_pairs = [v for v in file_map.values() if v[0] is not None]
    if not matched_pairs:
        print("⚠️ No files matched to manifest")
        return False, []

    # ── DOWNLOAD COMBINED SIGNED PDF ─────────────────────────────────
    combined_bytes = signwell_service.download_completed_pdf(doc_id)
    if not combined_bytes:
        print(f"⚠️ Could not download combined PDF for {doc_id}")
        return False, []

    # ── SPLIT BY PAGE COUNTS ─────────────────────────────────────────
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        from PyPDF2 import PdfReader, PdfWriter

    combined_reader = PdfReader(_io.BytesIO(combined_bytes))
    total_pages = len(combined_reader.pages)
    expected_pages = sum(sf.get('pages_number', 0) for _, sf in matched_pairs)

    print(f"  Combined PDF: {total_pages}pp, expected from files: {expected_pages}pp")

    # Process files in SignWell order (accounts for signature pages in middle)
    page_offset = 0
    processed = []
    for idx in sorted(file_map.keys()):
        entry, sf = file_map[idx]
        page_count = sf.get('pages_number', 1)
        
        # Skip unmatched files (signature page, etc.) but still advance offset
        if entry is None:
            print(f"  ⏭️  Skipping {sf.get('name', '?')[:40]} at offset {page_offset} ({page_count}pp)")
            page_offset += page_count
            continue

        if page_offset + page_count > total_pages:
            print(f"  ⚠️ Page overflow at offset {page_offset}, file needs {page_count}pp but only {total_pages - page_offset} remain")
            page_count = total_pages - page_offset
            if page_count <= 0:
                continue

        category          = entry.get('category', 'auto')
        original_filename = entry.get('original_filename', 'document.pdf')

        # Extract pages for this file
        writer = PdfWriter()
        for p in range(page_offset, page_offset + page_count):
            writer.add_page(combined_reader.pages[p])
        buf = _io.BytesIO()
        writer.write(buf)
        segment_bytes = buf.getvalue()
        page_offset += page_count

        # Store under correct category
        storage_path = f"{safe_cn}/{category}/signed/{original_filename}"
        try:
            supabase.storage.from_('signed-documents').upload(
                storage_path, segment_bytes,
                {'content-type': 'application/pdf', 'x-upsert': 'true'}
            )
            # Delete existing row for this path then insert
            try:
                supabase.table('client_documents').delete().eq('storage_path', storage_path).execute()
            except Exception:
                pass
            supabase.table('client_documents').insert({
                'lead_id':           lead_id,
                'client_name':       client_name,
                'category':          category,
                'document_type':     'signed',
                'document_name':     original_filename,
                'original_filename': original_filename,
                'storage_path':      storage_path,
                'file_size':         len(segment_bytes),
                'mime_type':         'application/pdf',
                'uploaded_by':       'signwell_completion',
            }).execute()
            entry['signed_storage_path'] = storage_path
            entry['signed_at'] = datetime.now().isoformat()
            processed.append(original_filename)
            print(f"  ✅ Stored [{category}] {original_filename} ({page_count}pp)")
        except Exception as up_err:
            print(f"  ⚠️ Upload failed for {original_filename}: {up_err}")

    # ── UPDATE SEND LOG ──────────────────────────────────────────────
    supabase.table('signwell_send_log').update({
        'status':          'completed',
        'completed_at':    datetime.now().isoformat(),
        'files_manifest':  files_manifest,
    }).eq('signwell_document_id', doc_id).execute()

    # ── UPDATE LEGACY TABLE ──────────────────────────────────────────
    try:
        supabase.table('zoho_sign_requests').update({
            'status': 'completed',
        }).eq('request_id', doc_id).execute()
    except Exception:
        pass

    # ── BUILD BUNDLES ────────────────────────────────────────────────
    try:
        _build_bundles_for_client(client_name, lead_id)
    except Exception as be:
        print(f"⚠️ Bundle build failed: {be}")

    print(f"✅ Manifest completion done: {len(processed)}/{len(files_manifest)} files")
    return True, processed


@app.route('/api/signwell/webhook', methods=['POST'])
def signwell_webhook():
    """
    Receive SignWell document.completed webhook.
    Uses shared manifest completion handler to split + segregate signed PDFs.
    """
    if not _verify_signwell_webhook(request):
        print('❌ SignWell webhook auth failed')
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        payload = request.json or {}
        event_type = payload.get('event_type') or payload.get('type', '')
        print(f"📨 SignWell Webhook received: {event_type}")

        if event_type not in ('document.completed', 'document_completed'):
            return jsonify({'received': True, 'processed': False, 'reason': 'not a completion event'})

        document = payload.get('document') or payload.get('data', {}).get('document', {})
        doc_id = document.get('id') or payload.get('document_id', '')
        if not doc_id:
            return jsonify({'error': 'No document ID in webhook'}), 400

        ok, processed = _handle_manifest_completion(doc_id)
        return jsonify({'received': True, 'processed': len(processed), 'files': processed})

    except Exception as e:
        print(f"❌ Webhook error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/send-log', methods=['GET'])
def get_signwell_send_log():
    """Get send log entries for the Post-Sign Processing UI."""
    try:
        client_name = request.args.get('client_name')
        lead_id     = request.args.get('lead_id')
        query = supabase.table('signwell_send_log').select('*')
        if client_name:
            query = query.eq('client_name', client_name)
        if lead_id:
            query = query.eq('lead_id', lead_id)
        result = query.order('created_at', desc=True).limit(50).execute()

        # Attach signed URL preview links to each manifest entry
        logs = result.data or []
        for log in logs:
            for entry in log.get('files_manifest', []):
                sp = entry.get('signed_storage_path')
                if sp:
                    try:
                        su = supabase.storage.from_('signed-documents')\
                            .create_signed_url(sp, 3600)
                        entry['preview_url'] = su.get('signedURL', '')
                    except Exception:
                        entry['preview_url'] = ''
        return jsonify({'status': 'success', 'logs': logs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/recategorize', methods=['POST'])
def signwell_recategorize():
    """
    Manual override: move a signed document from one category to another.
    Copies the file in Supabase storage, updates client_documents, logs the change.
    This is the safety net for any system glitch in the token-matching logic.
    """
    try:
        data         = request.json or {}
        doc_id_param = data.get('client_doc_id')
        new_category = data.get('new_category')
        reason       = data.get('reason', 'manual override by agent')

        if not doc_id_param or not new_category:
            return jsonify({'error': 'client_doc_id and new_category required'}), 400
        if new_category not in ['auto', 'home']:
            return jsonify({'error': 'new_category must be "auto" or "home"'}), 400

        doc_res = supabase.table('client_documents').select('*')\
            .eq('id', doc_id_param).limit(1).execute()
        if not doc_res.data:
            return jsonify({'error': 'Document not found'}), 404

        doc          = doc_res.data[0]
        old_category = doc.get('category', '')
        old_path     = doc.get('storage_path', '')

        if old_category == new_category:
            return jsonify({'message': 'Already in that category', 'changed': False})

        # Build new path by replacing category segment
        new_path = old_path.replace(f'/{old_category}/', f'/{new_category}/', 1)

        # Copy to new path
        file_bytes = supabase.storage.from_('signed-documents').download(old_path)
        supabase.storage.from_('signed-documents').upload(
            new_path, file_bytes,
            {'content-type': 'application/pdf', 'x-upsert': 'true'}
        )

        # Update record
        update_payload = {
            'category':              new_category,
            'storage_path':          new_path,
        }
        # Add audit columns if they exist (won't error if they don't in some Supabase configs)
        try:
            supabase.table('client_documents').update(update_payload)\
                .eq('id', doc_id_param).execute()
        except Exception:
            pass

        # Remove old storage file
        try:
            supabase.storage.from_('signed-documents').remove([old_path])
        except Exception:
            pass

        print(f"✅ Recategorized: {doc.get('original_filename')} [{old_category}→{new_category}] — {reason}")
        return jsonify({
            'status':       'success',
            'old_category': old_category,
            'new_category': new_category,
            'new_path':     new_path,
        })

    except Exception as e:
        print(f"❌ Recategorize error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/document/<doc_id>', methods=['GET'])
def signwell_get_document(doc_id):
    """Get document status"""
    try:
        result = signwell_service.get_document(doc_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/document/<doc_id>/remind', methods=['POST'])
def signwell_remind(doc_id):
    """Send a signing reminder"""
    try:
        result = signwell_service.send_reminder(doc_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/document/<doc_id>/cancel', methods=['DELETE'])
def signwell_cancel(doc_id):
    """Cancel/delete a document"""
    try:
        result = signwell_service.delete_document(doc_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/document/<doc_id>/download', methods=['GET'])
def signwell_download(doc_id):
    """Download the completed (signed) PDF"""
    try:
        from flask import Response
        pdf_bytes = signwell_service.download_completed_pdf(doc_id)
        if pdf_bytes:
            return Response(
                pdf_bytes,
                mimetype='application/pdf',
                headers={'Content-Disposition': f'attachment; filename=signed_{doc_id}.pdf'}
            )
        return jsonify({'error': 'Could not download document'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/send-direct', methods=['POST'])
def signwell_send_direct():
    """Send files directly from browser (multipart form) via SignWell"""
    try:
        files = request.files.getlist('files')
        if not files:
            return jsonify({'error': 'No files provided'}), 400

        signer_name = request.form.get('signer_name', '')
        signer_email = request.form.get('signer_email', '')
        client_name = request.form.get('client_name', '')
        lead_id = request.form.get('lead_id', '')
        category = request.form.get('category', '')
        message = request.form.get('message', '')
        request_name = request.form.get('request_name', f'{client_name} - Insurance Documents')

        if not signer_email:
            return jsonify({'error': 'Signer email is required'}), 400

        # Save files to temp directory
        import tempfile
        temp_dir = tempfile.mkdtemp()
        temp_file_paths = []

        try:
            for f in files:
                safe_name = f.filename or 'document.pdf'
                temp_path = os.path.join(temp_dir, safe_name)
                f.save(temp_path)
                temp_file_paths.append(temp_path)

            print(f"📤 SignWell send-direct: {len(temp_file_paths)} files for {signer_email}")

            result = signwell_service.send_documents(
                file_paths=temp_file_paths,
                signer_name=signer_name,
                signer_email=signer_email,
                document_name=request_name,
                message=message,
            )

            return jsonify(result)

        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        print(f"❌ SignWell send-direct error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/webhook-legacy', methods=['POST'])
def signwell_webhook_legacy():
    if not _verify_signwell_webhook(request):
        print('❌ SignWell webhook-legacy auth failed')
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        webhook_data = request.json
        event_type = (webhook_data.get('event') or {}).get('type', '')
        doc_data = webhook_data.get('document') or {}
        doc_id = doc_data.get('id')
        print(f"📥 SignWell webhook: {event_type} | doc: {doc_id}")

        if event_type == 'document_completed' and doc_id:
            # ── Phase 3: Download signed PDF ─────────────────────────────
            signed_bytes = signwell_service.download_completed_pdf(doc_id)
            if not signed_bytes:
                print(f"⚠️  Could not download completed PDF for {doc_id}")
                return jsonify({'received': True, 'warning': 'PDF download failed'})

            # Lookup the originating request from DB to get lead_id + category
            db_rec = None
            try:
                rows = supabase.table('zoho_sign_requests').select('*').eq('request_id', doc_id).execute()
                db_rec = rows.data[0] if rows.data else None
            except Exception as dbe:
                print(f"⚠️  DB lookup failed: {dbe}")

            request_name = doc_data.get('name', 'Signed Document')
            lead_id     = (db_rec or {}).get('lead_id')
            client_name = (db_rec or {}).get('client_name') or (request_name.split('—')[1].strip() if '—' in request_name else 'Client')
            category    = (db_rec or {}).get('category', 'auto')

            # ── Fetch reference documents from Supabase ──────────────────
            ref_pdfs = []
            try:
                ref_query = supabase.table('client_documents')\
                    .select('*').in_('document_type', ['nonsigning', 'reference']).eq('category', category)
                if client_name: ref_query = ref_query.eq('client_name', client_name)
                if lead_id:     ref_query = ref_query.eq('lead_id', lead_id)
                ref_rows = ref_query.execute()
                for ref in (ref_rows.data or []):
                    try:
                        raw = supabase.storage.from_('signed-documents').download(ref['storage_path'])
                        if raw: ref_pdfs.append(raw)
                    except: pass
                print(f"📎 Reference docs fetched: {len(ref_pdfs)}")
            except Exception as rfe:
                print(f"⚠️  Reference fetch failed: {rfe}")

            # ── Merge signed PDF + reference PDFs ────────────────────────
            merged_bytes = None
            try:
                from pypdf import PdfWriter, PdfReader
                import io
                writer = PdfWriter()
                writer.append(PdfReader(io.BytesIO(signed_bytes)))
                for ref_raw in ref_pdfs:
                    writer.append(PdfReader(io.BytesIO(ref_raw)))
                buf = io.BytesIO()
                writer.write(buf)
                merged_bytes = buf.getvalue()
                print(f"✅ Merged PDF: {len(merged_bytes)} bytes ({1 + len(ref_pdfs)} parts)")
            except Exception as me:
                print(f"⚠️  Merge failed (sending signed only): {me}")
                merged_bytes = signed_bytes

            # ── Save signed PDF to Supabase storage ──────────────────────
            safe_client = client_name.replace(' ', '_').replace('/', '_')
            signed_path = f"{safe_client}/{category}/signed/{doc_id}_signed.pdf"
            bundle_path = f"{safe_client}/{category}/signed/{doc_id}_bundle.pdf"
            try:
                supabase.storage.from_('signed-documents').upload(
                    signed_path, signed_bytes,
                    {'content-type': 'application/pdf', 'upsert': 'true'}
                )
                print(f"✅ Signed PDF saved: {signed_path}")
            except Exception as se:
                print(f"⚠️  Sign PDF storage failed: {se}")

            if merged_bytes and merged_bytes != signed_bytes:
                try:
                    supabase.storage.from_('signed-documents').upload(
                        bundle_path, merged_bytes,
                        {'content-type': 'application/pdf', 'upsert': 'true'}
                    )
                    print(f"✅ Bundle saved: {bundle_path}")
                except Exception as be:
                    print(f"⚠️  Bundle storage failed: {be}")
                    bundle_path = None
            else:
                bundle_path = None

            # ── Record in client_documents table ─────────────────────────
            try:
                supabase.table('client_documents').delete().eq('storage_path', signed_path).execute()
            except Exception:
                pass
            try:
                supabase.table('client_documents').insert({
                    'lead_id': lead_id,
                    'client_name': client_name,
                    'category': category,
                    'document_type': 'signed_completed',
                    'document_name': request_name,
                    'original_filename': f"{doc_id}_signed.pdf",
                    'storage_path': signed_path,
                    'file_size': len(signed_bytes),
                    'mime_type': 'application/pdf',
                    'uploaded_by': 'signwell_webhook',
                }).execute()
            except Exception as dbe2:
                print(f"⚠️  DB insert signed_completed failed: {dbe2}")

            if bundle_path:
                try:
                    supabase.table('client_documents').delete().eq('storage_path', bundle_path).execute()
                except Exception:
                    pass
                try:
                    supabase.table('client_documents').insert({
                        'lead_id': lead_id,
                        'client_name': client_name,
                        'category': category,
                        'document_type': 'signed_bundle',
                        'document_name': f"{request_name} — Bundle",
                        'original_filename': f"{doc_id}_bundle.pdf",
                        'storage_path': bundle_path,
                        'file_size': len(merged_bytes),
                        'mime_type': 'application/pdf',
                        'uploaded_by': 'signwell_webhook',
                    }).execute()
                except Exception as dbe3:
                    print(f"⚠️  DB insert signed_bundle failed: {dbe3}")

            # ── Update zoho_sign_requests status ────────────────────────
            try:
                supabase.table('zoho_sign_requests').update({
                    'status': 'completed',
                    'signed_pdf_path': signed_path,
                    'bundle_pdf_path': bundle_path or '',
                }).eq('request_id', doc_id).execute()
            except Exception as dbu:
                # Column may not exist — try without the new columns
                try:
                    supabase.table('zoho_sign_requests').update({'status': 'completed'}).eq('request_id', doc_id).execute()
                except: pass

            # ── Email the combined packet ─────────────────────────────────
            try:
                import smtplib, ssl
                from email.message import EmailMessage
                smtp_server = os.getenv('MS_OFFICE_SMTP_SERVER', 'smtp.office365.com')
                smtp_port   = int(os.getenv('MS_OFFICE_SMTP_PORT', 587))
                sender      = os.getenv('MS_OFFICE_EMAIL', '')
                password    = os.getenv('MS_OFFICE_EMAIL_PASSWORD', '')
                recipient   = os.getenv('DELIVERY_EMAIL', os.getenv('MS_OFFICE_EMAIL', ''))

                if sender and password and recipient:
                    send_bytes = merged_bytes if merged_bytes else signed_bytes
                    msg = EmailMessage()
                    msg['Subject'] = f'Signed Documents Ready — {client_name} ({category.upper()})'
                    msg['From']    = sender
                    msg['To']      = recipient
                    msg.set_content(
                        f'The signing process has been completed for {client_name}.\n\n'
                        f'Category: {category.upper()}\n'
                        f'Document: {request_name}\n'
                        f'Reference docs merged: {len(ref_pdfs)}\n\n'
                        f'Please find the combined document packet attached.'
                    )
                    filename = f'{safe_client}_{category}_completed_packet.pdf'
                    msg.add_attachment(send_bytes, maintype='application', subtype='pdf', filename=filename)
                    ctx = ssl.create_default_context()
                    with smtplib.SMTP(smtp_server, smtp_port) as server:
                        server.starttls(context=ctx)
                        server.login(sender, password)
                        server.send_message(msg)
                    print(f"📧 Email sent to {recipient}")
                else:
                    print(f"⚠️  Email not sent: missing SMTP credentials in .env")
            except Exception as ee:
                print(f"⚠️  Email send failed: {ee}")

        elif event_type == 'document_declined':
            try:
                doc_id and supabase.table('zoho_sign_requests').update({'status': 'declined'}).eq('request_id', doc_id).execute()
            except: pass

        return jsonify({'received': True, 'event': event_type})
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# SIGNWELL POLL/SYNC  (run when webhook can't reach localhost)
# ─────────────────────────────────────────────

def _process_completed_doc(doc_id, doc_data, db_rec):
    """
    Shared helper: download signed PDF, merge with reference docs, save to
    Supabase storage, update zoho_sign_requests.  Called by webhook AND sync.
    Returns True on success.
    """
    signed_bytes = signwell_service.download_completed_pdf(doc_id)
    if not signed_bytes:
        print(f"⚠️  Could not download completed PDF for {doc_id}")
        return False

    request_name = doc_data.get('name', 'Signed Document')
    lead_id     = (db_rec or {}).get('lead_id')
    client_name = (db_rec or {}).get('client_name') or 'Client'
    category    = (db_rec or {}).get('category', 'auto')
    safe_client = client_name.replace('/', '_').replace(' ', '_')

    # Save signed PDF to storage
    signed_path = f"{safe_client}/{category}/signed/{doc_id}_signed.pdf"
    try:
        supabase.storage.from_('signed-documents').remove([signed_path])
    except: pass
    try:
        supabase.storage.from_('signed-documents').upload(signed_path, signed_bytes,
            {"content-type": "application/pdf", "upsert": "true"})
        print(f"  ✓ Signed PDF saved: {signed_path}")
    except Exception as ue:
        print(f"  ⚠️  Storage upload signed PDF failed: {ue}")

    # Merge with reference docs
    merged_bytes = None
    ref_pdfs = []
    try:
        ref_rows = supabase.table('client_documents').select('*') \
            .eq('lead_id', lead_id).eq('category', category) \
            .in_('document_type', ['nonsigning', 'reference']).execute()
        for row in (ref_rows.data or []):
            sp = row.get('storage_path', '')
            if sp:
                fb = supabase.storage.from_('signed-documents').download(sp)
                if fb:
                    ref_pdfs.append(fb)
    except Exception as ref_err:
        print(f"  ⚠️  Ref doc fetch error: {ref_err}")

    if ref_pdfs:
        try:
            from pypdf import PdfWriter, PdfReader
            import io
            writer = PdfWriter()
            for b in [signed_bytes] + ref_pdfs:
                reader = PdfReader(io.BytesIO(b))
                for page in reader.pages:
                    writer.add_page(page)
            buf = io.BytesIO()
            writer.write(buf)
            merged_bytes = buf.getvalue()
        except Exception as me:
            print(f"  ⚠️  Merge error: {me}")

    # Save bundle
    bundle_path = f"{safe_client}/{category}/signed/{doc_id}_bundle.pdf"
    if merged_bytes:
        try:
            supabase.storage.from_('signed-documents').upload(bundle_path, merged_bytes,
                {"content-type": "application/pdf", "upsert": "true"})
            print(f"  ✓ Bundle saved: {bundle_path}")
        except Exception as ue2:
            print(f"  ⚠️  Bundle upload failed: {ue2}")
    else:
        bundle_path = ''

    # Update DB
    update_payload = {'status': 'completed'}
    try:
        update_payload['signed_pdf_path'] = signed_path
        if bundle_path:
            update_payload['bundle_pdf_path'] = bundle_path
        update_payload['completed_at'] = datetime.now(timezone.utc).isoformat()
    except: pass

    try:
        supabase.table('zoho_sign_requests').update(update_payload).eq('request_id', doc_id).execute()
    except Exception as dbu:
        # Fallback without new columns
        try:
            supabase.table('zoho_sign_requests').update({'status': 'completed'}).eq('request_id', doc_id).execute()
        except: pass

    # Delete-then-insert into client_documents (idempotent — safe for repeated webhook/sync calls)
    for dtype, path, raw_bytes in [
        ('signed_completed', signed_path, signed_bytes),
        ('signed_bundle', bundle_path, merged_bytes),
    ]:
        if not path or not raw_bytes:
            continue
        try:
            supabase.table('client_documents').delete().eq('storage_path', path).execute()
        except Exception:
            pass
        try:
            supabase.table('client_documents').insert({
                'lead_id': lead_id, 'client_name': client_name, 'category': category,
                'document_type': dtype,
                'document_name': f"{request_name} — {'Bundle' if dtype == 'signed_bundle' else 'Signed'}",
                'original_filename': path.split('/')[-1],
                'storage_path': path, 'file_size': len(raw_bytes),
                'mime_type': 'application/pdf', 'uploaded_by': 'signwell_sync',
            }).execute()
        except Exception as dbe:
            print(f"  ⚠️  client_documents insert ({dtype}) failed: {dbe}")

    print(f"✅ Doc {doc_id[:16]} processed as completed")
    return True


@app.route('/api/signwell/sync-status', methods=['POST'])
def signwell_sync_status():
    """
    Poll SignWell API for all pending documents and process any that are now
    completed.  Use this when the webhook can't reach localhost.
    Checks BOTH signwell_send_log (manifest) and zoho_sign_requests (legacy).
    """
    try:
        updated = []
        errors  = []
        checked_ids = set()

        # ── PATH 1: signwell_send_log (manifest-based, preferred) ────
        try:
            log_rows = supabase.table('signwell_send_log').select('*')\
                .eq('status', 'pending').execute()
            for log in (log_rows.data or []):
                doc_id = log.get('signwell_document_id')
                if not doc_id:
                    continue
                checked_ids.add(doc_id)
                try:
                    res = signwell_service.get_document(doc_id)
                    if not res.get('success'):
                        errors.append({'id': doc_id, 'error': res.get('error')})
                        continue
                    sw_status = (res['document'].get('status') or '').lower()
                    print(f"  🔍 {doc_id[:16]} → {sw_status} (manifest path)")

                    if sw_status == 'completed':
                        ok, processed = _handle_manifest_completion(doc_id)
                        if ok:
                            updated.append({'id': doc_id, 'name': res['document'].get('name', ''),
                                            'status': 'completed', 'files': processed})
                        else:
                            errors.append({'id': doc_id, 'error': 'manifest completion failed'})
                    elif sw_status in ('declined', 'voided', 'expired'):
                        supabase.table('signwell_send_log').update({'status': 'declined'})\
                            .eq('signwell_document_id', doc_id).execute()
                        try:
                            supabase.table('zoho_sign_requests').update({'status': 'declined'})\
                                .eq('request_id', doc_id).execute()
                        except Exception:
                            pass
                        updated.append({'id': doc_id, 'status': 'declined'})
                except Exception as ex:
                    errors.append({'id': doc_id, 'error': str(ex)})
        except Exception as log_err:
            print(f"⚠️ signwell_send_log query failed: {log_err}")

        # ── PATH 2: zoho_sign_requests (legacy, no manifest) ────────
        rows = supabase.table('zoho_sign_requests').select('*') \
            .in_('status', ['pending', 'sent', 'awaiting_signatures']).execute()

        for db_rec in (rows.data or []):
            doc_id = db_rec.get('request_id')
            if not doc_id or doc_id in checked_ids:
                continue
            checked_ids.add(doc_id)
            try:
                res = signwell_service.get_document(doc_id)
                if not res.get('success'):
                    errors.append({'id': doc_id, 'error': res.get('error')})
                    continue

                doc = res['document']
                sw_status = (doc.get('status') or '').lower()
                print(f"  🔍 {doc_id[:16]} → {sw_status} (legacy path)")

                if sw_status == 'completed':
                    # Try manifest first, fall back to legacy
                    ok, processed = _handle_manifest_completion(doc_id)
                    if ok:
                        updated.append({'id': doc_id, 'name': doc.get('name', ''),
                                        'status': 'completed', 'files': processed})
                    else:
                        # Legacy: single combined PDF
                        ok2 = _process_completed_doc(doc_id, doc, db_rec)
                        if ok2:
                            updated.append({'id': doc_id, 'name': doc.get('name', ''),
                                            'status': 'completed'})
                        else:
                            errors.append({'id': doc_id, 'error': 'PDF download/process failed'})

                elif sw_status in ('declined', 'voided', 'expired'):
                    try:
                        supabase.table('zoho_sign_requests').update({'status': 'declined'}) \
                            .eq('request_id', doc_id).execute()
                        updated.append({'id': doc_id, 'status': 'declined'})
                    except Exception:
                        pass

            except Exception as ex:
                errors.append({'id': doc_id, 'error': str(ex)})

        return jsonify({
            'success': True,
            'checked': len(checked_ids),
            'updated': len(updated),
            'updates': updated,
            'errors': errors,
        })

    except Exception as e:
        print(f"❌ Sync-status error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/signwell/document-status/<doc_id>', methods=['GET'])
def signwell_document_status(doc_id):
    """Get live status of a single SignWell document directly from their API."""
    try:
        res = signwell_service.get_document(doc_id)
        if not res.get('success'):
            return jsonify({'error': res.get('error')}), 404
        doc = res['document']
        return jsonify({
            'success': True,
            'id': doc.get('id'),
            'name': doc.get('name'),
            'status': doc.get('status'),
            'created_at': doc.get('created_at'),
            'completed_at': doc.get('completed_at'),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# SEND SIGNED EMAIL (Microsoft 365 SMTP)
# ─────────────────────────────────────────────

@app.route('/api/email-config', methods=['GET'])
def email_config():
    """Return non-sensitive email config so the UI can show the From address."""
    sender = os.getenv('MS_OFFICE_EMAIL', '')
    configured = bool(sender and os.getenv('MS_OFFICE_EMAIL_PASSWORD', ''))
    return jsonify({
        'sender': sender,
        'configured': configured,
        'smtp_server': os.getenv('MS_OFFICE_SMTP_SERVER', 'smtp.office365.com'),
    })


@app.route('/api/email-preview-attachments', methods=['POST'])
def email_preview_attachments():
    """
    Preview the list of attachments that will be sent for a given request_id.
    Returns: { attachments: [{name, type, size_kb}], count }
    """
    try:
        data = request.get_json() or {}
        request_id = data.get('request_id', '').strip()

        if not request_id:
            return jsonify({'error': 'request_id is required'}), 400

        # Fetch DB record
        rows = supabase.table('zoho_sign_requests').select('*') \
            .eq('request_id', request_id).limit(1).execute()
        if not rows.data:
            return jsonify({'error': 'Document record not found'}), 404
        rec = rows.data[0]

        client_name = rec.get('client_name', 'Unknown Client')
        bucket_folder = rec.get('bucket_folder') or ''
        lead_id = rec.get('lead_id')

        attachments = []

        # 1. Signed PDF
        try:
            sp = (rec.get('signed_pdf_path') or
                  (f"{bucket_folder}/signed/{request_id}_signed.pdf" if bucket_folder else None))
            if sp:
                try:
                    data_bytes = supabase.storage.from_('signed-documents').download(sp)
                    if data_bytes:
                        attachments.append({
                            'name': f"{client_name.replace(' ', '_')}_signed.pdf",
                            'type': 'signed',
                            'size_kb': round(len(data_bytes) / 1024, 1)
                        })
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Bundle PDF
        try:
            bp = (rec.get('bundle_pdf_path') or
                  (f"{bucket_folder}/signed/{request_id}_bundle.pdf" if bucket_folder else None))
            sp_used = rec.get('signed_pdf_path') or (f"{bucket_folder}/signed/{request_id}_signed.pdf" if bucket_folder else '')
            if bp and bp != sp_used:
                try:
                    data_bytes = supabase.storage.from_('signed-documents').download(bp)
                    if data_bytes:
                        attachments.append({
                            'name': f"{client_name.replace(' ', '_')}_bundle.pdf",
                            'type': 'bundle',
                            'size_kb': round(len(data_bytes) / 1024, 1)
                        })
                except Exception:
                    pass
        except Exception:
            pass

        # 3. Reference documents from client_documents
        try:
            q = supabase.table('client_documents').select(
                'document_name,original_filename,storage_path,document_type'
            ).eq('client_name', client_name)
            if lead_id:
                q = q.eq('lead_id', str(lead_id))
            # Exclude signed documents - only get uploaded/reference docs
            ref_rows = q.neq('document_type', 'signed').limit(15).execute()
            for ref in (ref_rows.data or []):
                try:
                    path = ref.get('storage_path') or ''
                    if not path:
                        continue
                    # All docs are in 'signed-documents' bucket (the only bucket)
                    rdata = supabase.storage.from_('signed-documents').download(path)
                    if rdata:
                        fname = ref.get('document_name') or ref.get('original_filename') or os.path.basename(path)
                        attachments.append({
                            'name': fname,
                            'type': 'reference',
                            'size_kb': round(len(rdata) / 1024, 1)
                        })
                except Exception as ref_err:
                    print(f"⚠️  Skipping reference doc {ref.get('document_name', 'unknown')}: {ref_err}")
        except Exception as ex:
            print(f"⚠️  Reference docs query error: {ex}")

        return jsonify({
            'success': True,
            'attachments': attachments,
            'count': len(attachments)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/send-category-email', methods=['POST'])
def send_category_email():
    """
    Send ALL documents for a specific client + category (auto or home).
    Attaches: signed docs + reference/nonsigning docs for that category.
    Body: { client_name, lead_id, category, to, cc, subject, body }
    """
    try:
        import msal, base64 as _b64

        data        = request.get_json() or {}
        client_name = data.get('client_name', '').strip()
        lead_id     = data.get('lead_id', '').strip()
        category    = (data.get('category') or '').strip().upper()
        to_addr     = data.get('to', '').strip()
        cc_addr     = data.get('cc', '').strip()
        subject     = data.get('subject', '').strip()
        body_text      = data.get('body', '').strip()
        excluded_paths = set(data.get('excluded_paths') or [])

        if not client_name:
            return jsonify({'error': 'client_name is required'}), 400
        if not category or category not in ['AUTO', 'HOME', 'HOMEOWNERS', 'RENTERS', 'RENTAL']:
            return jsonify({'error': 'category must be AUTO, HOME, HOMEOWNERS, RENTERS, or RENTAL'}), 400
        if not to_addr:
            return jsonify({'error': 'Recipient email (to) is required'}), 400

        tenant_id     = os.getenv('AZURE_TENANT_ID', '')
        client_id     = os.getenv('AZURE_CLIENT_ID', '')
        sender        = os.getenv('MS_OFFICE_EMAIL', '')
        refresh_token = os.getenv('MS_GRAPH_REFRESH_TOKEN', '')

        if not all([tenant_id, client_id, sender]):
            return jsonify({'error': 'Azure/Graph credentials not configured'}), 400
        if not refresh_token:
            return jsonify({'error': 'Not authenticated. Run: python backend/graph_login.py'}), 400

        # ── Acquire Graph access token ──────────────────────
        msal_app = msal.PublicClientApplication(
            client_id,
            authority=f'https://login.microsoftonline.com/{tenant_id}'
        )
        token_result = msal_app.acquire_token_by_refresh_token(
            refresh_token,
            scopes=['https://graph.microsoft.com/Mail.Send']
        )
        if 'access_token' not in token_result:
            err_detail = token_result.get('error_description', str(token_result))
            return jsonify({'error': f'Graph token refresh failed: {err_detail}'}), 500

        access_token = token_result['access_token']
        if token_result.get('refresh_token'):
            import re as _re
            env_path = os.path.join(os.path.dirname(__file__), '.env')
            try:
                with open(env_path, 'r', encoding='utf-8') as _f:
                    _env = _f.read()
                _env = _re.sub(r'MS_GRAPH_REFRESH_TOKEN=.*',
                               f'MS_GRAPH_REFRESH_TOKEN={token_result["refresh_token"]}', _env)
                with open(env_path, 'w', encoding='utf-8') as _f:
                    _f.write(_env)
            except Exception:
                pass

        # ── Default subject / body ─────────────────────────
        if not subject:
            subject = f'{category} Documents — {client_name}'
        if not body_text:
            body_text = (
                f'Hi,\n\nPlease find all {category} insurance documents for '
                f'{client_name} attached.\n\nThis email was sent from Auto Dashboard.'
            )

        # ── Collect ALL documents for this client+category ──
        # Query ALL rows for this client + category (lowercase) in one shot
        cat_lower = category.lower()
        print(f"📧 Email: querying client_documents for client={client_name!r} category={cat_lower!r}")

        q = supabase.table('client_documents').select(
            'document_name,original_filename,storage_path,document_type'
        ).eq('client_name', client_name).eq('category', cat_lower)
        if lead_id:
            q = q.eq('lead_id', lead_id)
        all_rows = q.execute()
        all_docs = all_rows.data or []
        print(f"📧 Email: found {len(all_docs)} total rows in DB")

        # Deduplicate by storage_path, separate into signed vs reference
        seen_paths = set()
        signed_docs = []
        ref_docs = []
        for doc in all_docs:
            path = doc.get('storage_path') or ''
            if not path or path in seen_paths or path in excluded_paths:
                continue
            seen_paths.add(path)
            if doc.get('document_type') == 'signed':
                signed_docs.append(doc)
            elif doc.get('document_type') in ('nonsigning', 'reference'):
                ref_docs.append(doc)
            # Skip 'signing' type — those are the unsigned originals

        print(f"📧 Email: {len(signed_docs)} signed, {len(ref_docs)} reference (after dedup)")

        # ── Download and encode attachments ────────────────
        # ALL files live in the 'signed-documents' bucket
        graph_attachments = []
        attachment_names = []
        signed_count = 0
        ref_count = 0

        for doc in signed_docs:
            path = doc.get('storage_path', '')
            fname = doc.get('document_name') or doc.get('original_filename') or os.path.basename(path)
            try:
                file_bytes = supabase.storage.from_('signed-documents').download(path)
                if file_bytes:
                    graph_attachments.append({
                        '@odata.type': '#microsoft.graph.fileAttachment',
                        'name': fname,
                        'contentType': 'application/pdf',
                        'contentBytes': _b64.b64encode(file_bytes).decode('utf-8')
                    })
                    attachment_names.append(fname)
                    signed_count += 1
                    print(f"  + signed: {fname} ({len(file_bytes)} bytes)")
                else:
                    print(f"  ! empty download: {path}")
            except Exception as e:
                print(f"  ! download failed {path}: {e}")

        for doc in ref_docs:
            path = doc.get('storage_path', '')
            fname = doc.get('document_name') or doc.get('original_filename') or os.path.basename(path)
            try:
                file_bytes = supabase.storage.from_('signed-documents').download(path)
                if file_bytes:
                    graph_attachments.append({
                        '@odata.type': '#microsoft.graph.fileAttachment',
                        'name': fname,
                        'contentType': 'application/pdf',
                        'contentBytes': _b64.b64encode(file_bytes).decode('utf-8')
                    })
                    attachment_names.append(fname)
                    ref_count += 1
                    print(f"  + ref: {fname} ({len(file_bytes)} bytes)")
                else:
                    print(f"  ! empty download: {path}")
            except Exception as e:
                print(f"  ! download failed {path}: {e}")

        if not graph_attachments:
            return jsonify({'error': f'No {category} documents found for {client_name}'}), 404

        print(f"📧 Email: attaching {len(graph_attachments)} files (signed={signed_count}, refs={ref_count})")

        # ── Build and send via Graph API ───────────────────
        to_recipients = [{'emailAddress': {'address': a.strip()}}
                         for a in to_addr.split(',') if a.strip()]
        cc_recipients = [{'emailAddress': {'address': a.strip()}}
                         for a in cc_addr.split(',') if cc_addr and a.strip()]

        mail_payload = {
            'message': {
                'subject': subject,
                'body': {'contentType': 'Text', 'content': body_text},
                'toRecipients': to_recipients,
                'ccRecipients': cc_recipients,
                'attachments': graph_attachments,
            },
            'saveToSentItems': True
        }

        resp = requests.post(
            'https://graph.microsoft.com/v1.0/me/sendMail',
            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
            json=mail_payload, timeout=60
        )

        if resp.status_code == 202:
            print(f"📧 SENT {category} email -> {to_addr} | {len(attachment_names)} attachments")
            return jsonify({
                'success': True,
                'sent_to': to_addr,
                'sent_from': sender,
                'category': category,
                'attachments': attachment_names,
                'count': len(attachment_names),
                'signed': signed_count,
                'references': ref_count,
            })
        else:
            err_body = ''
            try:
                err_body = resp.json().get('error', {}).get('message', resp.text[:300])
            except Exception:
                err_body = resp.text[:300]
            print(f"❌ Graph sendMail HTTP {resp.status_code}: {err_body}")
            return jsonify({'error': f'Graph API error ({resp.status_code}): {err_body}'}), 500

    except ImportError:
        return jsonify({'error': 'msal package not installed. Run: pip install msal'}), 500
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"❌ send-category-email error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/email-preview-attachments-category', methods=['POST'])
def email_preview_attachments_category():
    """
    Preview all documents for a specific client + category (AUTO/HOME).
    Returns metadata for display in email modal before sending.
    Body: { client_name, lead_id, category }
    """
    try:
        data = request.get_json() or {}
        client_name = data.get('client_name', '').strip()
        lead_id     = data.get('lead_id', '').strip()
        category    = data.get('category', '').strip().upper()

        if not client_name or not category:
            return jsonify({'error': 'client_name and category are required'}), 400

        cat_lower = category.lower()
        q = supabase.table('client_documents') \
            .select('document_name,original_filename,storage_path,document_type') \
            .eq('client_name', client_name) \
            .eq('category', cat_lower)
        if lead_id:
            q = q.eq('lead_id', lead_id)
        all_rows = q.execute()

        attachments = []
        seen_paths = set()
        for doc in (all_rows.data or []):
            path = doc.get('storage_path', '')
            dtype = doc.get('document_type', '')
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            if dtype == 'signed':
                attachments.append({
                    'name': doc.get('document_name') or doc.get('original_filename') or 'Unknown.pdf',
                    'type': 'Signed Document',
                    'size_kb': '-',
                    'tag': 'signed',
                    'storage_path': path
                })
            elif dtype in ('nonsigning', 'reference'):
                attachments.append({
                    'name': doc.get('document_name') or doc.get('original_filename') or 'Unknown.pdf',
                    'type': dtype.replace('_', ' ').title(),
                    'size_kb': '-',
                    'tag': 'reference',
                    'storage_path': path
                })

        return jsonify({
            'success': True,
            'attachments': attachments,
            'count': len(attachments),
            'client_name': client_name,
            'category': category
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/category-documents-preview', methods=['POST'])
def category_documents_preview():
    """
    Get all documents (signed + reference/nonsigning) for a client + category.
    Returns document metadata with signed URLs for PDF viewing.
    Uses ONLY the 'signed-documents' bucket (the only bucket that exists).
    Body: { client_name, lead_id, category }
    """
    try:
        data = request.get_json() or {}
        client_name = data.get('client_name', '').strip()
        lead_id     = data.get('lead_id', '').strip()
        category    = data.get('category', '').strip()
        cat_lower   = category.lower()

        if not client_name or not category:
            return jsonify({'error': 'client_name and category are required'}), 400

        # ── Single query: all docs for this client + category ──
        resp = supabase.table('client_documents') \
            .select('*') \
            .eq('client_name', client_name) \
            .eq('category', cat_lower) \
            .execute()
        all_rows = resp.data or []
        print(f"[preview] {len(all_rows)} rows for {client_name} / {cat_lower}")

        documents = []
        seen_paths = set()

        for doc in all_rows:
            doc_type = (doc.get('document_type') or '').lower()
            storage_path = doc.get('storage_path', '')

            # Skip signing copies (unsigned originals) — not useful for preview
            if doc_type == 'signing':
                continue
            # Dedup by storage_path
            if not storage_path or storage_path in seen_paths:
                continue
            seen_paths.add(storage_path)

            # Determine display tag
            if doc_type == 'signed':
                tag = 'signed'
            else:
                tag = 'reference'

            # Generate preview URL from the ONLY bucket: signed-documents
            preview_url = ''
            try:
                url_resp = supabase.storage.from_('signed-documents') \
                    .create_signed_url(storage_path, 3600)
                preview_url = url_resp.get('signedURL', '') if url_resp else ''
            except Exception as url_err:
                print(f"  [preview] URL fail {storage_path}: {url_err}")

            documents.append({
                'id': doc.get('id'),
                'document_name': doc.get('document_name') or doc.get('original_filename') or 'Unknown.pdf',
                'original_filename': doc.get('original_filename'),
                'document_type': tag,
                'storage_path': storage_path,
                'preview_url': preview_url,
                'file_size': doc.get('file_size', 0),
                'created_at': doc.get('created_at'),
            })

        print(f"[preview] Returning {len(documents)} documents for {client_name} / {cat_lower}")

        return jsonify({
            'success': True,
            'documents': documents,
            'count': len(documents),
            'client_name': client_name,
            'category': category
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[preview] ERROR: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/send-signed-email', methods=['POST'])
def send_signed_email():
    """
    Compose and send the signed document packet (signed PDF + bundle + reference docs)
    via Microsoft Graph API (Mail.Send) — emails appear in Outlook Sent Items.
    Body: { request_id, to, cc, subject, body }
    """
    try:
        import msal, base64 as _b64

        data       = request.get_json() or {}
        request_id = data.get('request_id', '').strip()
        to_addr    = data.get('to', '').strip()
        cc_addr    = data.get('cc', '').strip()
        subject    = data.get('subject', '').strip()
        body_text  = data.get('body', '').strip()

        if not request_id:
            return jsonify({'error': 'request_id is required'}), 400
        if not to_addr:
            return jsonify({'error': 'Recipient email (to) is required'}), 400

        tenant_id     = os.getenv('AZURE_TENANT_ID', '')
        client_id     = os.getenv('AZURE_CLIENT_ID', '')
        sender        = os.getenv('MS_OFFICE_EMAIL', '')
        refresh_token = os.getenv('MS_GRAPH_REFRESH_TOKEN', '')

        if not all([tenant_id, client_id, sender]):
            return jsonify({
                'error': 'Azure/Graph credentials not fully configured. '
                         'Set AZURE_TENANT_ID, AZURE_CLIENT_ID and MS_OFFICE_EMAIL in backend/.env'
            }), 400

        if not refresh_token:
            return jsonify({
                'error': 'Not authenticated yet. Run graph_login.py once to complete '
                         'Microsoft sign-in: python backend/graph_login.py'
            }), 400

        # ── Acquire Graph access token via stored refresh token ──────
        msal_app = msal.PublicClientApplication(
            client_id,
            authority=f'https://login.microsoftonline.com/{tenant_id}'
        )
        token_result = msal_app.acquire_token_by_refresh_token(
            refresh_token,
            scopes=['https://graph.microsoft.com/Mail.Send']
        )
        if 'access_token' not in token_result:
            err_detail = token_result.get('error_description', str(token_result))
            return jsonify({
                'error': f'Graph token refresh failed: {err_detail}. '
                         'Re-run graph_login.py to re-authenticate.'
            }), 500

        access_token = token_result['access_token']
        # Save updated refresh token if one was returned
        if token_result.get('refresh_token'):
            import re as _re
            env_path = os.path.join(os.path.dirname(__file__), '.env')
            try:
                with open(env_path, 'r', encoding='utf-8') as _f:
                    _env = _f.read()
                _env = _re.sub(r'MS_GRAPH_REFRESH_TOKEN=.*',
                               f'MS_GRAPH_REFRESH_TOKEN={token_result["refresh_token"]}', _env)
                with open(env_path, 'w', encoding='utf-8') as _f:
                    _f.write(_env)
            except Exception:
                pass

        # ── Fetch DB record ───────────────────────────────
        rows = supabase.table('zoho_sign_requests').select('*') \
            .eq('request_id', request_id).limit(1).execute()
        if not rows.data:
            return jsonify({'error': 'Document record not found in database'}), 404
        rec = rows.data[0]

        client_name   = rec.get('client_name', 'Unknown Client')
        category      = (rec.get('category') or '').upper()
        doc_name      = rec.get('document_name') or rec.get('request_name') or 'Document'
        bucket_folder = rec.get('bucket_folder') or ''
        lead_id       = rec.get('lead_id')

        # ── Default subject / body ─────────────────────────
        if not subject:
            subject = f'Signed Documents — {client_name}' + (f' ({category})' if category else '')
        if not body_text:
            body_text = (
                f'Hi,\n\n'
                f'The signing process has been completed for the following client:\n\n'
                f'  Client:    {client_name}\n'
                f'  Document:  {doc_name}\n'
                f'  Category:  {category or "N/A"}\n\n'
                f'Please find the signed document package (signed PDF and reference documents) attached.\n\n'
                f'This email was sent from Auto Dashboard.'
            )

        graph_attachments = []
        attachment_names  = []
        safe_name = client_name.replace(' ', '_')

        def _attach(data_bytes, filename):
            if data_bytes:
                graph_attachments.append({
                    '@odata.type' : '#microsoft.graph.fileAttachment',
                    'name'        : filename,
                    'contentType' : 'application/pdf',
                    'contentBytes': _b64.b64encode(data_bytes).decode('utf-8')
                })
                attachment_names.append(filename)

        # 1. Signed PDF
        try:
            sp = (rec.get('signed_pdf_path') or
                  (f"{bucket_folder}/signed/{request_id}_signed.pdf" if bucket_folder else None))
            if sp:
                _attach(supabase.storage.from_('signed-documents').download(sp),
                        f'{safe_name}_signed.pdf')
        except Exception as ex:
            print(f"⚠️  signed PDF attach: {ex}")

        # 2. Bundle PDF
        try:
            bp = (rec.get('bundle_pdf_path') or
                  (f"{bucket_folder}/signed/{request_id}_bundle.pdf" if bucket_folder else None))
            sp_used = rec.get('signed_pdf_path') or (f"{bucket_folder}/signed/{request_id}_signed.pdf" if bucket_folder else '')
            if bp and bp != sp_used:
                _attach(supabase.storage.from_('signed-documents').download(bp),
                        f'{safe_name}_bundle.pdf')
        except Exception as ex:
            print(f"⚠️  bundle PDF attach: {ex}")

        # 3. Reference / uploaded docs from client_documents
        ref_count = 0
        try:
            q = supabase.table('client_documents').select(
                'document_name,original_filename,storage_path,document_type'
            ).eq('client_name', client_name)
            if lead_id:
                q = q.eq('lead_id', str(lead_id))
            # Exclude signed documents - only attach uploaded/reference docs
            ref_rows = q.neq('document_type', 'signed').limit(15).execute()
            for ref in (ref_rows.data or []):
                try:
                    path = ref.get('storage_path') or ''
                    if not path:
                        continue
                    # All docs are in 'signed-documents' bucket (the only bucket)
                    rdata = supabase.storage.from_('signed-documents').download(path)
                    if rdata:
                        fname = ref.get('document_name') or ref.get('original_filename') or os.path.basename(path)
                        _attach(rdata, fname)
                        ref_count += 1
                except Exception as ref_err:
                    print(f"⚠️  ref doc attach: {ref_err}")
        except Exception as ex:
            print(f"⚠️  reference docs query: {ex}")

        # ── Build Graph API message payload ────────────────
        to_recipients = [{'emailAddress': {'address': a.strip()}}
                         for a in to_addr.split(',') if a.strip()]
        cc_recipients = [{'emailAddress': {'address': a.strip()}}
                         for a in cc_addr.split(',') if cc_addr and a.strip()]

        mail_payload = {
            'message': {
                'subject' : subject,
                'body'    : {'contentType': 'Text', 'content': body_text},
                'toRecipients': to_recipients,
                'ccRecipients': cc_recipients,
                'attachments' : graph_attachments,
            },
            'saveToSentItems': True
        }

        # ── Send via Graph API ─────────────────────────────
        graph_url = 'https://graph.microsoft.com/v1.0/me/sendMail'
        headers   = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type' : 'application/json'
        }
        resp = requests.post(graph_url, headers=headers, json=mail_payload, timeout=60)

        if resp.status_code == 202:
            print(f"📧 Graph email sent → {to_addr} | attachments={len(attachment_names)} (refs={ref_count})")
            return jsonify({
                'success'    : True,
                'sent_to'    : to_addr,
                'sent_from'  : sender,
                'attachments': attachment_names,
                'count'      : len(attachment_names),
            })
        else:
            err_body = ''
            try:
                err_body = resp.json().get('error', {}).get('message', resp.text[:300])
            except Exception:
                err_body = resp.text[:300]
            print(f"❌ Graph sendMail HTTP {resp.status_code}: {err_body}")
            return jsonify({
                'error': f'Graph API error ({resp.status_code}): {err_body}'
            }), 500

    except ImportError:
        return jsonify({'error': 'msal package not installed. Run: pip install msal'}), 500
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"❌ send-signed-email error: {e}")
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# SIGNED DOCUMENTS VIEWER
# ─────────────────────────────────────────────

@app.route('/signed-documents')
def signed_documents_viewer():
    """Serve the signed documents viewer UI"""
    return send_from_directory(app.static_folder, 'signed-documents.html')


@app.route('/api/signed-documents', methods=['GET'])
def list_signed_documents():
    """List all signing requests with status + signed/bundle download URLs"""
    try:
        category = request.args.get('category')
        lead_id  = request.args.get('lead_id')
        status   = request.args.get('status')
        STATUS_NORM = {
            'pending': 'pending', 'awaiting_signatures': 'pending', 'sent': 'pending',
            'draft': 'pending', 'in_progress': 'pending',
            'completed': 'completed', 'declined': 'declined', 'voided': 'declined',
            'expired': 'declined',
        }

        query = supabase.table('zoho_sign_requests').select('*').order('created_at', desc=True)
        if category:
            query = query.eq('category', category)
        if lead_id:
            query = query.eq('lead_id', lead_id)
        if status:
            query = query.eq('status', status)
        result = query.execute()

        records = []
        for rec in (result.data or []):
            item = dict(rec)
            # Normalize status for frontend display
            raw_status = (item.get('status') or 'pending').lower()
            item['status'] = STATUS_NORM.get(raw_status, 'pending')
            # Alias recipient_name/email → also expose as signer_name/email for backward compat
            item['signer_name']  = item.get('recipient_name', '')
            item['signer_email'] = item.get('recipient_email', '')
            # Generate signed download URLs for completed docs
            for path_key, url_key in [('signed_pdf_path', 'signed_pdf_url'), ('bundle_pdf_path', 'bundle_pdf_url')]:
                path = item.get(path_key, '') or ''
                if path:
                    try:
                        signed = supabase.storage.from_('signed-documents').create_signed_url(path, 3600)
                        item[url_key] = signed.get('signedURL', '')
                    except:
                        item[url_key] = ''
                else:
                    item[url_key] = ''
            # Fallback: look up in client_documents by request_id
            if not item.get('signed_pdf_url') and item.get('request_id'):
                try:
                    cdocs = supabase.table('client_documents').select('*') \
                        .eq('document_type', 'signed_completed') \
                        .like('storage_path', f'%{item["request_id"]}%').execute()
                    if cdocs.data:
                        sp = cdocs.data[0]['storage_path']
                        su = supabase.storage.from_('signed-documents').create_signed_url(sp, 3600)
                        item['signed_pdf_url'] = su.get('signedURL', '')
                        item['signed_pdf_path'] = sp
                except:
                    pass
            if not item.get('bundle_pdf_url') and item.get('request_id'):
                try:
                    cdocs = supabase.table('client_documents').select('*') \
                        .eq('document_type', 'signed_bundle') \
                        .like('storage_path', f'%{item["request_id"]}%').execute()
                    if cdocs.data:
                        sp = cdocs.data[0]['storage_path']
                        su = supabase.storage.from_('signed-documents').create_signed_url(sp, 3600)
                        item['bundle_pdf_url'] = su.get('signedURL', '')
                        item['bundle_pdf_path'] = sp
                except:
                    pass
            records.append(item)

        return jsonify({'success': True, 'count': len(records), 'records': records})
    except Exception as e:
        print(f"❌ Error listing signed documents: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/signed-documents/<request_id>/download', methods=['GET'], endpoint='signwell_download_signed_document')
def signwell_download_signed_document(request_id):
    """Download a signed or bundle PDF by request_id. ?type=signed|bundle"""
    try:
        doc_type = request.args.get('type', 'bundle')
        col = 'bundle_pdf_path' if doc_type == 'bundle' else 'signed_pdf_path'

        # Try zoho_sign_requests first
        path = ''
        try:
            rows = supabase.table('zoho_sign_requests').select(col).eq('request_id', request_id).execute()
            path = (rows.data[0].get(col, '') if rows.data else '') or ''
        except:
            pass

        # Fallback: look in client_documents
        if not path:
            dtype = 'signed_bundle' if doc_type == 'bundle' else 'signed_completed'
            try:
                cdocs = supabase.table('client_documents').select('storage_path') \
                    .eq('document_type', dtype) \
                    .like('storage_path', f'%{request_id}%').execute()
                path = cdocs.data[0]['storage_path'] if cdocs.data else ''
            except:
                pass

        if not path:
            return jsonify({'error': 'Document not found'}), 404

        file_bytes = supabase.storage.from_('signed-documents').download(path)
        if not file_bytes:
            return jsonify({'error': 'Could not download file'}), 404

        from flask import Response as FlaskResponse
        fname = path.split('/')[-1]
        return FlaskResponse(
            file_bytes,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{fname}"'}
        )
    except Exception as e:
        print(f"❌ Download signed doc error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/signed-documents/stats', methods=['GET'])
def signed_documents_stats():
    """Return counts by status for the dashboard header"""
    try:
        result = supabase.table('zoho_sign_requests').select('status').execute()
        rows = result.data or []
        # Normalize any SignWell / legacy status strings to our 3 buckets
        STATUS_NORM = {
            'pending': 'pending', 'awaiting_signatures': 'pending', 'sent': 'pending',
            'draft': 'pending', 'in_progress': 'pending',
            'completed': 'completed', 'declined': 'declined', 'voided': 'declined',
            'expired': 'declined',
        }
        stats = {'total': len(rows), 'completed': 0, 'pending': 0, 'declined': 0}
        for r in rows:
            s = (r.get('status') or 'pending').lower()
            bucket = STATUS_NORM.get(s, 'pending')
            stats[bucket] += 1
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Create tables if they don't exist
    try:
        # Check if leads table exists, if not this will fail gracefully
        supabase.table('leads').select('*').limit(1).execute()
    except:
        print("Note: Ensure 'leads' table exists in Supabase")
    
    port = int(os.getenv('FLASK_PORT', 5000))
    is_debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
    # socketio.run() is required (not app.run()) so SocketIO transports work locally.
    # In production Railway uses: gunicorn -k eventlet -w 1
    socketio.run(app, debug=is_debug, port=port, host='0.0.0.0', use_reloader=False)
