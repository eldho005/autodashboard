"""
Quick Meta Event Sync Test - Non-interactive
"""

import requests
import json
from datetime import datetime

# Configuration - EDIT THESE
BACKEND_URL = "http://localhost:5000"  # LOCAL TEST
LEAD_ID = "TEST91745"  # Change to your actual lead ID
EMAIL = "policy@meta.com"  # Your login email  
PASSWORD = "policy@123"  # Your login password

print(f"\n{'='*60}")
print(f"🧪 META EVENT SYNC TEST")
print(f"{'='*60}")
print(f"🌐 Backend: {BACKEND_URL}")
print(f"🆔 Lead ID: {LEAD_ID}")
print(f"👤 Email: {EMAIL}")
print(f"⏰ Test Time: {datetime.now()}")
print(f"{'='*60}\n")

# Create session to persist cookies
session = requests.Session()

# Step 1: Login to get auth token
print(f"🔐 Logging in...")
login_url = f"{BACKEND_URL}/api/login"
login_payload = {"email": EMAIL, "password": PASSWORD}

try:
    login_response = session.post(login_url, json=login_payload, timeout=10)
    if login_response.status_code == 200:
        print(f"✅ Login successful!\n")
    else:
        print(f"❌ Login failed: {login_response.status_code}")
        print(login_response.text)
        exit(1)
except Exception as e:
    print(f"❌ Login error: {str(e)}")
    exit(1)

# Step 2: Test Meta event sync
url = f"{BACKEND_URL}/api/leads/{LEAD_ID}/sync-event"

# Request payload
payload = {
    "event_type": "QualifiedLead",
}

print(f"📤 Sending POST request to: {url}")
print(f"📋 Payload: {json.dumps(payload, indent=2)}\n")

try:
    # Send request with authenticated session
    response = session.post(url, json=payload, timeout=30)
    
    print(f"{'─'*60}")
    print(f"📥 RESPONSE")
    print(f"{'─'*60}")
    print(f"Status Code: {response.status_code}\n")
    
    # Parse response
    try:
        result = response.json()
        print(f"Response Body:")
        print(json.dumps(result, indent=2))
        
        # Check for success
        if response.status_code == 200 and result.get('success'):
            print(f"\n{'='*60}")
            print(f"✅ SUCCESS!")
            print(f"{'='*60}")
            
            meta_response = result.get('meta_response', {})
            events_received = meta_response.get('events_received', 0)
            
            if events_received > 0:
                print(f"🎉 Meta received the event! ({events_received} event(s))")
                print(f"🔍 FB Trace ID: {meta_response.get('fbtrace_id', 'N/A')}")
                print(f"\n✨ Event should appear in Meta Events Manager within 5-10 minutes")
                print(f"📊 Check here: https://business.facebook.com/events_manager2")
            else:
                print(f"⚠️ Meta API accepted request but events_received = 0")
                print(f"🔍 This usually means:")
                print(f"   - Token missing ads_management permission")
                print(f"   - Data validation failed")
                print(f"   - Pixel ID mismatch")
                print(f"\n📋 Meta Messages: {meta_response.get('messages', [])}")
                print(f"🔍 FB Trace ID: {meta_response.get('fbtrace_id', 'N/A')}")
        else:
            print(f"\n{'='*60}")
            print(f"❌ FAILED")
            print(f"{'='*60}")
            print(f"Error: {result.get('error', 'Unknown error')}")
            
    except json.JSONDecodeError:
        print(f"Response Body (raw):")
        print(response.text)
        
except requests.exceptions.ConnectionError as e:
    print(f"\n❌ CONNECTION ERROR")
    print(f"Could not connect to {BACKEND_URL}")
    print(f"Error: {str(e)}")
    
except requests.exceptions.Timeout:
    print(f"\n⏱️ REQUEST TIMEOUT")
    print(f"Request took longer than 30 seconds")
    
except Exception as e:
    print(f"\n❌ ERROR: {str(e)}")
    import traceback
    traceback.print_exc()

print(f"\n{'='*60}")
print(f"Test completed")
print(f"{'='*60}\n")
