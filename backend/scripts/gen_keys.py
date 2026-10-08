"""Print fresh secrets for .env:  python -m scripts.gen_keys"""
import base64
import secrets

print("SECRET_KEY=" + secrets.token_urlsafe(48))
print("ENCRYPTION_KEY=" + base64.b64encode(secrets.token_bytes(32)).decode())
print("HMAC_KEY=" + secrets.token_urlsafe(48))
print("META_VERIFY_TOKEN=" + secrets.token_urlsafe(24))
