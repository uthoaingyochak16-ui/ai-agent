"""The ONLY bridge from database rows to prompt inputs. It reduces a profile to a ProfileView
(no phone/email, no encrypted columns, no pending values) before anything touches the PrivacyGuard."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile, ProfileFact
from app.privacy.summary import ProfileView
from app.services import consent as consent_svc
from app.services.profile_fields import STAFF_ONLY_KEYS


async def load_profile_view(session: AsyncSession, profile: Profile) -> ProfileView:
    facts = (await session.execute(select(ProfileFact).where(ProfileFact.profile_id == profile.id)
                                   .order_by(ProfileFact.updated_at.desc()))).scalars().all()
    pending = {f.key[len("pending_"):] for f in facts if f.key.startswith("pending_")}
    shown = tuple((f.key, f.value) for f in facts
                  if not f.key.startswith(("pending_", "merge_")) and f.key not in STAFF_ONLY_KEYS)  # staff notes stay internal
    return ProfileView(
        name=profile.name, location=profile.location, age=profile.age, profession=profile.profession,
        language=profile.language, facts=shown,
        has_phone=bool(profile.phone_hash) or "phone" in pending,
        has_email=bool(profile.email_hash) or "email" in pending,
        consent_state=consent_svc.consent_state(profile), pending=frozenset(pending))
