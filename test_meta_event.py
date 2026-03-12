"""
Test Meta Event Sync - Quick test script
Run this after updating META_PAGE_ACCESS_TOKEN in Railway
"""

import requests
import json
from datetime import datetime

# Configuration
BACKEND_URL = input("Enter backend URL (or press Enter for localhost:5000): ").strip() or "http://localhost:5000"
LEAD_ID = input("Enter Lead ID to test (or press Enter for TEST91745): ").strip() or "TEST91745"

print(f"\n{'='*60}")
print(f"🧪 META EVENT SYNC TEST")
print(f"{'='*60}")
print(f"🌐 Backend: {BACKEND_URL}")
print(f"🆔 Lead ID: {LEAD_ID}")
print(f"⏰ Test Time: {datetime.now()}")
print(f"{'='*60}\n")

# Test endpoint
url = f"{BACKEND_URL}/api/leads/{LEAD_ID}/sync-event"

# Request payload
payload = {
    "event_type": "QualifiedLead",  # Or let it auto-detect
    # "test_event_code": "TEST91745"  # Uncomment to send to Test Events instead of real Events
}

print(f"📤 Sending POST request to: {url}")
print(f"📋 Payload: {json.dumps(payload, indent=2)}\n")

try:
    # Send request
    response = requests.post(url, json=payload, timeout=30)
    
    print(f"{'─'*60}")
    print(f"📥 RESPONSE")
    print(f"{'─'*60}")
    print(f"Status Code: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}\n")
    
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
                print(f"   - Data validation failed (check field formats)")
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
        
except requests.exceptions.ConnectionError:
    print(f"\n❌ CONNECTION ERROR")
    print(f"Could not connect to {BACKEND_URL}")
    print(f"\nMake sure:")
    print(f"  1. Backend Flask server is running")
    print(f"  2. URL is correct (localhost:5000 for local, Railway URL for production)")
    
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
