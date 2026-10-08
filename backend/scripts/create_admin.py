"""Usage: python -m scripts.create_admin --email you@example.com --password '...' [--name Name] [--role admin]"""
import argparse
import asyncio

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models import AdminUser


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--name", default="Admin")
    ap.add_argument("--role", choices=["admin", "agent"], default="admin")
    a = ap.parse_args()
    if len(a.password) < 10:
        raise SystemExit("password must be at least 10 characters")
    async with get_sessionmaker()() as s:
        u = (await s.execute(select(AdminUser).where(AdminUser.email == a.email.lower()))).scalar_one_or_none()
        if u:
            u.password_hash, u.role, u.active = hash_password(a.password), a.role, True
        else:
            s.add(AdminUser(email=a.email.lower(), name=a.name, role=a.role, password_hash=hash_password(a.password)))
        await s.commit()
    print(f"{'updated' if u else 'created'} {a.role}: {a.email}")


asyncio.run(main())
