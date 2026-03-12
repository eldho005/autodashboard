import requests, json

s = requests.Session()
s.post('http://localhost:5000/api/login', json={'email': 'policy@meta.com', 'password': 'policy@123'})

r2 = s.get('http://localhost:5000/api/leads?limit=100')
leads = r2.json()
leads = leads if isinstance(leads, list) else leads.get('leads', [])
qualified = [l for l in leads if l.get('is_auto_qualified')]
lead = qualified[0]
lead_id = lead['id']
name = lead.get('name', 'N/A')
print(f'Sending Lead event for: {name} (ID: {lead_id})')

sync_r = s.post(
    f'http://localhost:5000/api/leads/{lead_id}/sync-event',
    json={'event_type': 'QualifiedLead', 'test_event_code': 'TEST91745'}
)
resp = sync_r.json()
meta = resp.get('meta_response', {})
print(f'HTTP status: {sync_r.status_code}')
print(f'Meta events_received: {meta.get("events_received", 0)}')
print(f'fbtrace_id: {meta.get("fbtrace_id")}')
print(f'messages: {meta.get("messages")}')
print()
print('>>> Open this URL in your browser to see the event:')
print('https://business.facebook.com/events_manager2/list/pixel/2251357192000496/test_events')
print()
print('Enter test code: TEST91745')
print('The Lead event will appear there within seconds.')
