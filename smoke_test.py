"""Basic manual smoke-test checklist; run against a locally running API."""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"

def request(path, method="GET", data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data is not None else None, headers=headers, method=method)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

if __name__ == "__main__":
    print("Health:", request("/health"))
    print("API root:", request("/"))
    print("Manual flow: register recruiter and candidate; login; create/publish job; candidate applies; upload PDF/DOCX; recruiter changes status and schedules interview; inspect analytics.")
