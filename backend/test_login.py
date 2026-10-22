from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
r = client.post('/api/auth/login', data={'username':'board@senus.com','password':'senus123'})
print(r.status_code, r.json())
