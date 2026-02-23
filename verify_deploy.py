import requests, json, time
BASE='https://recruitment-backend-82519464499.us-central1.run.app'

# Health check
r = requests.get(f'{BASE}/health')
h = r.json()
print(f"Health: {h['status']} | Version: {h['version']} | Candidates: {h['database']['candidates']}")

# Login
r = requests.post(f'{BASE}/api/auth/login', json={'email':'admin@developer.com','password':'Maahir@12'})
token = r.json()['token']
headers = {'Authorization': f'Bearer {token}'}

# Test candidates/new (the fixed endpoint)
r = requests.get(f'{BASE}/api/candidates/new?since=2026-02-20T00:00:00', headers=headers)
print(f"candidates/new: {r.status_code}")
if r.status_code == 200:
    d = r.json()
    print(f"  new_count={d['new_count']}, returned={d['returned']}")
else:
    print(f"  Error: {r.text[:300]}")

# Test stats
r = requests.get(f'{BASE}/api/stats', headers=headers)
print(f"/api/stats: {r.status_code}")
