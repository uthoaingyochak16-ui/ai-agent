from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core import crypto
from app.models import ChannelIdentity, Conversation, Profile, ProfileFact, ProfileMergeLog
from app.services import consent as C
from app.services.identity import handle_shared_contacts, resolve
from app.services.merge import MergeError, merge_profiles, unmerge_profiles
from app.services.settings_service import set_setting
from tests.conftest import mk_msg


async def user(session, channel, uid, text="hi", mid=None):
    r = await resolve(session, mk_msg(channel, uid, text, mid or f"{channel}-{uid}-{text[:5]}"))
    await session.commit()
    return r


async def test_first_message_creates_anonymous_profile_and_conversation(session):
    r = await user(session, "messenger", "PSID1")
    assert r.is_new_identity and r.profile.name is None and not r.profile.consent_given
    assert r.conversation.profile_id == r.profile.id


async def test_same_identity_reuses_profile_and_conversation(session):
    a = await user(session, "messenger", "PSID1", mid="a")
    b = await user(session, "messenger", "PSID1", mid="b")
    assert a.profile.id == b.profile.id and a.conversation.id == b.conversation.id and not b.is_new_identity


async def test_phone_is_held_encrypted_until_consent_then_saved(session):
    r = await user(session, "messenger", "PSID1")
    await handle_shared_contacts(session, r, "my number is 01712345678")
    await session.commit()
    p = await session.get(Profile, r.profile.id)
    assert p.phone_hash is None and p.phone_enc is None
    pend = (await session.execute(select(ProfileFact).where(ProfileFact.key == "pending_phone"))).scalar_one()
    assert "1712345678" not in pend.value  # encrypted at rest
    await C.record_consent(session, p, True)
    await session.commit()
    assert C.get_phone(p) == "+8801712345678"
    assert p.phone_hash == crypto.blind_index("+8801712345678", "phone")
    assert (await session.execute(select(ProfileFact).where(ProfileFact.key == "pending_phone"))).first() is None


async def test_declined_consent_discards_pending_and_never_saves(session):
    r = await user(session, "messenger", "PSID1")
    await handle_shared_contacts(session, r, "a@b.com")
    await C.record_consent(session, r.profile, False)
    await handle_shared_contacts(session, r, "ok my phone is 01712345678")
    await session.commit()
    assert r.profile.phone_enc is None and r.profile.email_enc is None
    assert (await session.execute(select(ProfileFact).where(ProfileFact.key.like("pending%")))).first() is None
    assert C.consent_state(r.profile) == "declined"


async def test_cross_channel_merge_via_shared_phone_with_whatsapp_identity(session):
    await set_setting(session, "merge_policy", "auto")
    wa = await user(session, "whatsapp", "8801712345678", text="salam")
    await C.record_consent(session, wa.profile, True)
    await C.apply_field(session, wa.profile, "name", "Karim")
    ms = await user(session, "messenger", "PSID9", text="hello")
    assert ms.profile.id != wa.profile.id
    await handle_shared_contacts(session, ms, "you can call me on 01712345678")
    await session.commit()
    idents = (await session.execute(select(ChannelIdentity))).scalars().all()
    assert {i.profile_id for i in idents} == {wa.profile.id}
    assert ms.profile.id == wa.profile.id and ms.profile.name == "Karim"
    convs = (await session.execute(select(Conversation))).scalars().all()
    assert {c.profile_id for c in convs} == {wa.profile.id}
    assert (await session.execute(select(ProfileMergeLog))).scalar_one().reason == "shared_phone"
    assert C.get_phone(await session.get(Profile, wa.profile.id)) == "+8801712345678"  # saved after merge (consented)


async def test_cross_channel_merge_via_shared_email(session):
    await set_setting(session, "merge_policy", "auto")
    a = await user(session, "messenger", "A")
    await C.record_consent(session, a.profile, True)
    await handle_shared_contacts(session, a, "me@Example.com")
    b = await user(session, "instagram", "B")
    await handle_shared_contacts(session, b, "reach me: ME@example.com")
    await session.commit()
    assert b.profile.id == a.profile.id


async def test_whatsapp_number_links_to_existing_profile_on_first_message(session):
    a = await user(session, "messenger", "A")
    await C.record_consent(session, a.profile, True)
    await handle_shared_contacts(session, a, "01712345678")
    await session.commit()
    w = await user(session, "whatsapp", "8801712345678", text="hi")
    assert w.profile.id == a.profile.id


async def test_default_policy_is_suggest_a_typed_phone_number_never_adopts_someone_elses_profile(session):
    wa = await user(session, "whatsapp", "8801712345678")
    ms = await user(session, "messenger", "PSID9")
    await handle_shared_contacts(session, ms, "01712345678")
    await session.commit()
    assert ms.profile.id != wa.profile.id
    f = (await session.execute(select(ProfileFact).where(ProfileFact.key == "merge_suggestion"))).scalar_one()
    assert f.value == str(wa.profile.id)


async def test_manual_merge_combines_facts_newest_wins_and_unmerge_restores(session):
    a = await user(session, "messenger", "A")
    b = await user(session, "instagram", "B")
    A, B = a.profile, b.profile
    for p in (A, B):
        await C.record_consent(session, p, True)
    A.location, B.location = "Dhaka", "Chattogram"
    A.profession = "Teacher"
    B.age = 30
    session.add_all([ProfileFact(profile_id=A.id, key="interest", value="yoga"),
                     ProfileFact(profile_id=B.id, key="interest", value="meditation"),
                     ProfileFact(profile_id=B.id, key="budget", value="low")])
    await session.commit()
    # make B the newer profile
    B.updated_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    await session.commit()
    log = await merge_profiles(session, A.id, B.id, actor="admin@x", reason="manual")
    await session.commit()
    A2 = await session.get(Profile, A.id)
    assert A2.location == "Chattogram" and A2.profession == "Teacher" and A2.age == 30
    facts = {f.key: f.value for f in (await session.execute(select(ProfileFact).where(ProfileFact.profile_id == A.id))).scalars()}
    assert facts["budget"] == "low" and facts["interest"] in ("yoga", "meditation")
    assert (await session.get(Profile, B.id)).merged_into_id == A.id
    warnings = await unmerge_profiles(session, log.id, actor="admin@x")
    await session.commit()
    assert warnings == []
    A3, B3 = await session.get(Profile, A.id), await session.get(Profile, B.id)
    assert A3.location == "Dhaka" and A3.age is None and B3.merged_into_id is None and B3.location == "Chattogram"
    idents = {i.external_id: i.profile_id for i in (await session.execute(select(ChannelIdentity))).scalars()}
    assert idents == {"A": A.id, "B": B.id}
    facts = {f.key: f.value for f in (await session.execute(select(ProfileFact).where(ProfileFact.profile_id == A.id))).scalars()}
    assert facts == {"interest": "yoga"}
    with pytest.raises(MergeError):
        await unmerge_profiles(session, log.id)


async def test_unmerge_restores_phone_hash_without_unique_violation(session):
    a = await user(session, "messenger", "A")
    b = await user(session, "instagram", "B")
    for p in (a.profile, b.profile):
        await C.record_consent(session, p, True)
    await C.apply_field(session, b.profile, "phone", "01712345678")
    await session.commit()
    log = await merge_profiles(session, a.profile.id, b.profile.id)
    await session.commit()
    assert (await session.get(Profile, a.profile.id)).phone_hash and (await session.get(Profile, b.profile.id)).phone_hash is None
    await unmerge_profiles(session, log.id)
    await session.commit()
    assert (await session.get(Profile, a.profile.id)).phone_hash is None
    assert (await session.get(Profile, b.profile.id)).phone_hash is not None


async def test_cannot_merge_self_or_already_merged(session):
    a = await user(session, "messenger", "A")
    b = await user(session, "messenger", "B")
    c = await user(session, "messenger", "C")
    with pytest.raises(MergeError):
        await merge_profiles(session, a.profile.id, a.profile.id)
    await merge_profiles(session, a.profile.id, b.profile.id)
    with pytest.raises(MergeError):
        await merge_profiles(session, c.profile.id, b.profile.id)


async def test_sensitive_fields_are_never_stored(session):
    r = await user(session, "messenger", "A")
    await C.record_consent(session, r.profile, True)
    assert await C.apply_field(session, r.profile, "password", "hunter2") == "rejected"
    assert await C.apply_field(session, r.profile, "otp_code", "123456") == "rejected"
    assert await C.apply_field(session, r.profile, "note", "4111 1111 1111 1111") == "rejected"
    assert await C.apply_field(session, r.profile, "age", "200") == "rejected"
    assert await C.apply_field(session, r.profile, "age", "34") == "saved" and r.profile.age == 34
    assert await C.apply_field(session, r.profile, "hobby", "chess") == "saved"


async def test_extracted_personal_fields_wait_for_consent(session):
    r = await user(session, "messenger", "A")
    assert await C.apply_field(session, r.profile, "name", "Rahim") == "pending"
    assert r.profile.name is None
    await C.record_consent(session, r.profile, True)
    assert r.profile.name == "Rahim"


async def test_erasure_wipes_everything_personal(session):
    a = await user(session, "messenger", "A")
    b = await user(session, "instagram", "B")
    await C.record_consent(session, a.profile, True)
    await C.apply_field(session, a.profile, "name", "Rahim")
    await C.apply_field(session, a.profile, "phone", "01712345678")
    await C.apply_field(session, a.profile, "hobby", "chess")
    await merge_profiles(session, a.profile.id, b.profile.id)
    await session.commit()
    await C.erase_profile(session, a.profile.id)
    await session.commit()
    p = await session.get(Profile, a.profile.id)
    assert p.name is None and p.phone_enc is None and p.phone_hash is None and p.erased_at and not p.consent_given
    assert (await session.execute(select(ProfileFact))).first() is None
    assert (await session.execute(select(ProfileMergeLog))).first() is None


@pytest.mark.parametrize("text,expected", [("yes", "yes"), ("হ্যাঁ", "yes"), ("Ji", "yes"), ("ok!", "yes"),
                                           ("না", "no"), ("no thanks", "no"), ("maybe later when I think", None),
                                           ("what are your prices?", None)])
def test_consent_reply_classifier(text, expected):
    assert C.classify_consent_reply(text) == expected


@pytest.mark.parametrize("text", ["please delete my data", "Erase my personal information", "forget me",
                                  "আমার তথ্য মুছে ফেলুন"])
def test_erasure_detection(text):
    assert C.is_erasure_request(text)
