import requests
import json

s = requests.Session()
r = s.post('http://localhost:5000/api/login', json={'email': 'policy@meta.com', 'password': 'policy@123'})
print('Login:', r.status_code, r.json().get('success'))

r2 = s.get('http://localhost:5000/api/leads?limit=100')
data = r2.json()
# API may return list directly or wrapped in object
leads = data if isinstance(data, list) else data.get('leads', [])
print(f'Total leads returned: {len(leads)}')

if leads:
    keys = list(leads[0].keys())
    print(f'Fields: {keys}')
    
    # Find qualified leads
    qualified = [l for l in leads if l.get('is_auto_qualified') == True or l.get('is_auto_qualified') == 1]
    print(f'Qualified leads found: {len(qualified)}')
    
    for l in leads[:5]:
        lead_id = l.get('id') or l.get('lead_id')
        name = l.get('full_name') or l.get('name', 'N/A')
        status = l.get('status', 'N/A')
        qualified_flag = l.get('is_auto_qualified', 'N/A')
        print(f'  id={lead_id} | qualified={qualified_flag} | status={status} | name={name}')

    if qualified:
        lead = qualified[0]
        lead_id = lead.get('id')
        print(f'\n--- Testing sync on qualified lead: {lead_id} ---')
        sync_r = s.post(f'http://localhost:5000/api/leads/{lead_id}/sync-event',
                        json={'event_type': 'QualifiedLead'})
        print(f'Sync status: {sync_r.status_code}')
        print(f'Sync response: {json.dumps(sync_r.json(), indent=2)}')
    else:
        # Try with the first lead anyway
        lead_id = leads[0].get('id')
        print(f'\n--- No qualified leads, testing sync on first lead: {lead_id} ---')
        sync_r = s.post(f'http://localhost:5000/api/leads/{lead_id}/sync-event',
                        json={'event_type': 'QualifiedLead'})
        print(f'Sync status: {sync_r.status_code}')
        print(f'Sync response: {json.dumps(sync_r.json(), indent=2)}')
