"""
Direct Meta Conversions API Test
Sends a test event directly to Meta's API - bypasses Flask backend entirely.
This confirms whether the token + pixel ID work.
"""

import requests
import json
import hashlib
import time

# Load from .env.local
FB_PIXEL_ID = "2251357192000496"
META_ACCESS_TOKEN = "EAATh87VBbpsBQ6vERax8b1Q8skSKtxMFwoss81xJRvzGRm33pjNPiORb5V1ZBbdpwr9vZBPGQcmOj7hNsAHnDC9xmKdIldaxcULZAUfTPzvxqK3tKLX0v4xaYWZCs5LkAf26u49vNwQhf71VztcN2SwxQKew6RA0aaSayRdpXEzM2CpEKVeAJqLqhrPD35JPfZA0GFhrI8QocFSJrw5xZAJLPIpunfqAN6hze7"

# Test event code from user
TEST_EVENT_CODE = "TEST91745"

def sha256_hash(value):
    """Hash a value using SHA-256 (Meta requires hashed PII)"""
    if not value:
        return ""
    return hashlib.sha256(value.strip().lower().encode('utf-8')).hexdigest()

print(f"\n{'='*60}")
print(f"🧪 DIRECT META CONVERSIONS API TEST")
print(f"{'='*60}")
print(f"📍 Pixel ID: {FB_PIXEL_ID}")
print(f"🔑 Token: {META_ACCESS_TOKEN[:20]}...{META_ACCESS_TOKEN[-10:]}")
print(f"🏷️  Test Event Code: {TEST_EVENT_CODE}")
print(f"{'='*60}\n")

# Step 1: Verify token permissions
print("📋 Step 1: Checking token permissions...")
debug_url = f"https://graph.facebook.com/debug_token?input_token={META_ACCESS_TOKEN}&access_token={META_ACCESS_TOKEN}"
try:
    debug_resp = requests.get(debug_url, timeout=10)
    debug_data = debug_resp.json()
    if 'data' in debug_data:
        token_data = debug_data['data']
        print(f"   App ID: {token_data.get('app_id', 'N/A')}")
        print(f"   Type: {token_data.get('type', 'N/A')}")
        print(f"   Valid: {token_data.get('is_valid', 'N/A')}")
        scopes = token_data.get('scopes', [])
        print(f"   Scopes: {', '.join(scopes) if scopes else 'None found'}")
        
        if 'ads_management' in scopes:
            print(f"   ✅ ads_management permission FOUND")
        else:
            print(f"   ❌ ads_management permission MISSING - events will be rejected!")
    else:
        print(f"   ⚠️ Could not debug token: {debug_data}")
except Exception as e:
    print(f"   ⚠️ Token debug failed: {str(e)}")

print()

# Step 2: Send test event to Meta Conversions API
print("📤 Step 2: Sending test event to Meta Conversions API...")

url = f"https://graph.facebook.com/v21.0/{FB_PIXEL_ID}/events"

# Build event payload with hashed test data
event_payload = {
    "data": [
        {
            "event_name": "Lead",
            "event_time": int(time.time()),
            "action_source": "website",
            "event_source_url": "https://web-production-3824b.up.railway.app",
            "user_data": {
                "em": [sha256_hash("test@example.com")],
                "ph": [sha256_hash("+14165551234")],
                "fn": [sha256_hash("test")],
                "ln": [sha256_hash("user")],
                "ct": [sha256_hash("toronto")],
                "st": [sha256_hash("on")],
                "zp": [sha256_hash("m5v2t6")],
                "country": [sha256_hash("ca")],
                "external_id": [sha256_hash("test-lead-001")],
                "client_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            "custom_data": {
                "currency": "CAD",
                "value": 150.00,
                "lead_event_source": "CRM",
                "event_source": "Auto Insurance Dashboard"
            }
        }
    ],
    "test_event_code": TEST_EVENT_CODE,
    "access_token": META_ACCESS_TOKEN
}

print(f"   URL: {url}")
print(f"   Event: Lead")
print(f"   Test Event Code: {TEST_EVENT_CODE}")
print(f"   Hashed Email: {sha256_hash('test@example.com')[:20]}...")
print()

try:
    response = requests.post(
        url,
        json=event_payload,
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    
    print(f"{'─'*60}")
    print(f"📥 META API RESPONSE")
    print(f"{'─'*60}")
    print(f"HTTP Status: {response.status_code}")
    
    result = response.json()
    print(f"Response: {json.dumps(result, indent=2)}")
    
    if response.status_code == 200:
        events_received = result.get('events_received', 0)
        fbtrace_id = result.get('fbtrace_id', 'N/A')
        messages = result.get('messages', [])
        
        print(f"\n{'='*60}")
        if events_received > 0:
            print(f"✅ SUCCESS! Meta received {events_received} event(s)")
            print(f"🔍 FB Trace ID: {fbtrace_id}")
            print(f"{'='*60}")
            print(f"\n🎉 Your new token is WORKING!")
            print(f"\n📊 To see the test event:")
            print(f"   1. Go to: https://business.facebook.com/events_manager2/list/pixel/{FB_PIXEL_ID}/test_events")
            print(f"   2. Enter Test Event Code: {TEST_EVENT_CODE}")
            print(f"   3. You should see the 'Lead' event appear there")
            print(f"\n💡 Note: Test events appear in 'Test Events' tab, not regular 'Events'")
        else:
            print(f"⚠️ Meta accepted the request but events_received = 0")
            print(f"🔍 FB Trace ID: {fbtrace_id}")
            print(f"{'='*60}")
            print(f"\n❌ Token likely MISSING ads_management permission")
            if messages:
                print(f"📋 Meta Messages:")
                for msg in messages:
                    print(f"   - {msg}")
    else:
        print(f"\n{'='*60}")
        print(f"❌ META API ERROR")
        print(f"{'='*60}")
        error = result.get('error', {})
        print(f"Error Type: {error.get('type', 'Unknown')}")
        print(f"Error Code: {error.get('code', 'N/A')}")
        print(f"Error Message: {error.get('message', 'N/A')}")
        print(f"FB Trace ID: {error.get('fbtrace_id', 'N/A')}")
        
        if error.get('code') == 190:
            print(f"\n🔑 Token is INVALID or EXPIRED. Generate a new one.")
        elif error.get('code') == 200:
            print(f"\n🔒 Permission error. Token needs ads_management permission.")
            
except Exception as e:
    print(f"\n❌ ERROR: {str(e)}")
    import traceback
    traceback.print_exc()

print(f"\n{'='*60}")
print(f"Test completed")
print(f"{'='*60}\n")
