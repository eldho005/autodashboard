"""
Run the Flask backend server locally
"""
import sys
import os

# Add parent directory to path so we can import backend module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Now import and run the app
from backend import app, socketio

if __name__ == '__main__':
    port = int(os.getenv('FLASK_PORT', 5000))
    print(f"\n{'='*60}")
    print(f"\U0001f680 Starting Auto Dashboard Backend")
    print(f"{'='*60}")
    print(f"\U0001f310 Server URL: http://localhost:{port}")
    print(f"\U0001f4ca Dashboard: http://localhost:{port}/")
    print(f"\U0001f527 API Base: http://localhost:{port}/api/")
    print(f"{'='*60}\n")
    socketio.run(app, host='0.0.0.0', port=port, debug=True, use_reloader=False)
