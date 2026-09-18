"""
One-shot Gmail sign-in. Run once before starting the service:

    python server/connect.py

Opens your browser for Google consent and writes server/token.json.
The service (gmail_service.py) then starts already-connected.
"""
from gmail_service import load_credentials, run_consent_flow, gmail_client

if __name__ == "__main__":
    creds = load_credentials()
    if creds:
        who = gmail_client(creds).users().getProfile(userId="me").execute()
        print(f"Already connected as {who.get('emailAddress')}. Delete server/token.json to switch accounts.")
    else:
        creds = run_consent_flow()
        who = gmail_client(creds).users().getProfile(userId="me").execute()
        print(f"Connected as {who.get('emailAddress')}. Token cached in server/token.json.")
