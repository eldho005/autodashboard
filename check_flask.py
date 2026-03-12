import requests
import time

print("Testing if Flask is running...")
time.sleep(2)

try:
    response = requests.get("http://localhost:5000/", timeout=5)
    print(f"✅ Flask is running! Status: {response.status_code}")
    print(f"Response: {response.text[:200]}")
except requests.exceptions.ConnectionError:
    print("❌ Flask is NOT running - connection refused")
except Exception as e:
    print(f"❌ Error: {str(e)}")
