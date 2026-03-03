"""
One-time Microsoft Graph login via device code flow.
Run this once to authenticate with MFA and save the refresh token to .env.
After this, the Auto Dashboard can send emails silently.
"""
import msal, os, sys, re
from dotenv import load_dotenv

load_dotenv('.env')

tenant_id = os.getenv('AZURE_TENANT_ID')
client_id = os.getenv('AZURE_CLIENT_ID')
sender    = os.getenv('MS_OFFICE_EMAIL')

if not all([tenant_id, client_id, sender]):
    print('ERROR: AZURE_TENANT_ID, AZURE_CLIENT_ID and MS_OFFICE_EMAIL must be set in .env')
    sys.exit(1)

print(f'\nLogging in as: {sender}')
print('─' * 50)

app = msal.PublicClientApplication(
    client_id,
    authority=f'https://login.microsoftonline.com/{tenant_id}'
)

# Initiate device code flow
flow = app.initiate_device_flow(scopes=['https://graph.microsoft.com/Mail.Send offline_access'])
if 'user_code' not in flow:
    print('ERROR: Failed to create device flow:', flow.get('error_description'))
    sys.exit(1)

print('\n  1. Open this URL in your browser:')
print(f'     {flow["verification_uri"]}')
print(f'\n  2. Enter this code: {flow["user_code"]}')
print('\n  3. Sign in with your Microsoft account (complete MFA if prompted)')
print('\nWaiting for you to complete sign-in...')

# Poll until user completes login
result = app.acquire_token_by_device_flow(flow)

if 'access_token' in result:
    refresh_token = result.get('refresh_token', '')
    print('\n✅ Login successful!')

    if not refresh_token:
        print('WARNING: No refresh token returned. Make sure offline_access scope is granted.')
        sys.exit(1)

    # Save refresh token to .env
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    with open(env_path, 'r', encoding='utf-8') as f:
        content = f.read()

    if 'MS_GRAPH_REFRESH_TOKEN=' in content:
        content = re.sub(r'MS_GRAPH_REFRESH_TOKEN=.*', f'MS_GRAPH_REFRESH_TOKEN={refresh_token}', content)
    else:
        content += f'\nMS_GRAPH_REFRESH_TOKEN={refresh_token}\n'

    with open(env_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print('✅ Refresh token saved to .env')
    print('\nYou can now send emails from Auto Dashboard without re-authenticating.')
    print('The token will auto-renew silently.')

else:
    print('\n❌ Login failed:', result.get('error_description', str(result))[:300])
    sys.exit(1)
