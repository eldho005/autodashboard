import requests, hashlib, time, json, os, sys

# Load token from .env.local
token = None
env_path = os.path.join(os.path.dirname(__file__), '.env.local')
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line.startswith('META_PAGE_ACCESS_TOKEN'):
            token = line.split('=', 1)[1].strip().strip('"').strip("'")
            break

if not token:
    print('ERROR: META_PAGE_ACCESS_TOKEN not found in .env.local')
    sys.exit(1)

print(f'Token: {token[:20]}...')

pixel_id = '2251357192000496'
url = f'https://graph.facebook.com/v21.0/{pixel_id}/events'

def sha256(val):
    return hashlib.sha256(val.strip().lower().encode()).hexdigest() if val else ''

ts = int(time.time())
payload = {
    'data': [{
        'event_name': 'QualifiedLead',
        'event_time': ts,
        'event_id': f'direct_test_{ts}',
        'action_source': 'system_generated',
        'user_data': {
            'em': sha256('ask.iqraa@gmail.com'),
            'ph': sha256('+14169893995'),
            'country': sha256('ca')
        },
        'custom_data': {
            'currency': 'CAD',
            'value': 0,
            'lead_status': 'qualified'
        }
    }],
    'access_token': token,
    'test_event_code': 'TEST91745'
}

print(f'\nSending QualifiedLead directly to Meta API...')
print(f'test_event_code: TEST91745')
print(f'action_source: system_generated')
print(f'event_time: {ts}')

r = requests.post(url, json=payload, timeout=30)
print(f'\nHTTP Status: {r.status_code}')
print(json.dumps(r.json(), indent=2))
