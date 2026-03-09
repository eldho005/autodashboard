"""
Quick script to find a qualified lead for testing
"""
from supabase import create_client
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv('../.env.local')

SUPABASE_URL = os.getenv('VITE_SUPABASE_URL')
SUPABASE_KEY = os.getenv('VITE_SUPABASE_SERVICE_ROLE_KEY')

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

print("Searching for qualified leads...")

# Find qualified leads
response = supabase.table('leads').select('id, name, email, phone, is_auto_qualified, premium, meta_lead_id').eq('is_auto_qualified', True).limit(5).execute()

if response.data:
    print(f"\n✅ Found {len(response.data)} qualified leads:\n")
    for lead in response.data:
        print(f"ID: {lead['id']}")
        print(f"Name: {lead.get('name', 'N/A')}")
        print(f"Email: {lead.get('email', 'N/A')}")
        print(f"Phone: {lead.get('phone', 'N/A')}")
        print(f"Premium: ${lead.get('premium', 0)}")
        print(f"Meta Lead ID: {lead.get('meta_lead_id', 'N/A')}")
        print(f"Qualified: {lead.get('is_auto_qualified', False)}")
        print("─" * 60)
else:
    print("\n❌ No qualified leads found. Creating a test lead...")
    
    # Create test lead
    test_lead = {
        'name': 'Test User',
        'email': 'test@example.com',
        'phone': '+14165551234',
        'is_auto_qualified': True,
        'premium': 150.00,
        'meta_lead_id': 'TEST91745',
        'form_id': os.getenv('META_LEAD_FORM_ID'),
        'created_at': 'now()'
    }
    
    create_response = supabase.table('leads').insert(test_lead).execute()
    if create_response.data:
        print(f"\n✅ Test lead created!")
        print(f"ID: {create_response.data[0]['id']}")
        print(f"Use this ID for testing Meta events")
    else:
        print(f"❌ Failed to create lead: {create_response}")
