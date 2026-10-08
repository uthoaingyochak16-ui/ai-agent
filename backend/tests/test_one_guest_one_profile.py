import httpx
import respx
from sqlalchemy import func, select

from app.models import ChannelIdentity, Profile, ProfileFact
from app.services.ingest import handle_message
from tests.conftest import configure_channel, mk_msg
from tests.test_admin_api2 import G, admin  # noqa: F401 - fixture

GRAPH = f"{G}/me/messages"


async def active_profiles(s):
    return (await s.execute(select(func.count()).select_from(Profile).where(Profile.merged_into_id.is_(None)))).scalar_one()


def mock_messenger(psid, name, other_ids=None):
    respx.get(f"{G}/{psid}").mock(return_value=httpx.Response(200, json={"first_name": name.split()[0], "last_name": name.split()[1]}))
    if other_ids is None:
        respx.get(f"{G}/{psid}/ids_for_pages").mock(return_value=httpx.Response(400, json={"error": {
            "code": 100, "message": "Can only query using page access tokens where the page is owned by the same business"}}))
    else:
        respx.get(f"{G}/{psid}/ids_for_pages").mock(return_value=httpx.Response(200, json={
            "data": [{"id": i, "page": {"id": "P2"}} for i in other_ids]}))


@respx.mock
async def test_whatsapp_number_finds_the_guest_who_typed_it_on_messenger(maker):
    async with maker() as s:
        await configure_channel(s)
        await configure_channel(s, channel="whatsapp", account_id="PN1")
    respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    respx.post(f"{G}/PN1/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "w"}]}))
    mock_messenger("PSID1", "Rahim Uddin")
    async with maker() as s:
        await handle_message(s, mk_msg(uid="PSID1", text="amar number 01712345678", mid="m1"))  # no consent yet
        await handle_message(s, mk_msg(channel="whatsapp", uid="8801712345678", text="hello", mid="w1", name="Rahim Uddin"))
        assert await active_profiles(s) == 1
        ids = (await s.execute(select(ChannelIdentity.profile_id))).scalars().all()
        assert len(set(ids)) == 1
        # the number itself is not stored before consent - only an unreadable keyed hash
        p = (await s.execute(select(Profile))).scalar_one()
        assert p.phone_hash is None and p.phone_enc is None


@respx.mock
async def test_same_number_but_different_name_is_only_suggested(maker):
    async with maker() as s:
        await configure_channel(s)
        await configure_channel(s, channel="whatsapp", account_id="PN1")
    respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    respx.post(f"{G}/PN1/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "w"}]}))
    mock_messenger("PSID2", "Rahim Uddin")
    async with maker() as s:
        await handle_message(s, mk_msg(uid="PSID2", text="call me 01812345678", mid="m2"))
        await handle_message(s, mk_msg(channel="whatsapp", uid="8801812345678", text="hello", mid="w2", name="Karim Mia"))
        assert await active_profiles(s) == 2
        assert (await s.execute(select(ProfileFact).where(ProfileFact.key == "merge_suggestion"))).scalar_one()


@respx.mock
async def test_meta_confirmed_same_person_on_another_page_is_merged(maker):
    async with maker() as s:
        await configure_channel(s)
    respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    mock_messenger("PAGE1_PSID", "Nadia Islam")
    mock_messenger("PAGE2_PSID", "Nadia Islam", other_ids=["PAGE1_PSID"])
    async with maker() as s:
        await handle_message(s, mk_msg(uid="PAGE1_PSID", text="hi", mid="a1"))
        await handle_message(s, mk_msg(uid="PAGE2_PSID", text="hi", mid="a2"))
        assert await active_profiles(s) == 1


@respx.mock
async def test_same_full_name_on_another_page_is_suggested_and_merged_with_one_click(client, admin, maker):  # noqa: F811
    async with maker() as s:
        await configure_channel(s)
    respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    mock_messenger("PSID_A", "Uthoaingyo Chak")
    mock_messenger("PSID_B", "Uthoaingyo Chak")
    async with maker() as s:
        await handle_message(s, mk_msg(uid="PSID_A", text="hi", mid="b1"))
        await handle_message(s, mk_msg(uid="PSID_B", text="hi", mid="b2"))
        assert await active_profiles(s) == 2
    sugg = (await client.get("/api/profiles/merge-suggestions", headers=admin)).json()
    assert len(sugg) == 1 and sugg[0]["names"] == ["Uthoaingyo Chak", "Uthoaingyo Chak"]
    r = await client.post("/api/profiles/merge", headers=admin, json={"keep_id": sugg[0]["suggested_with"], "merge_id": sugg[0]["profile_id"]})
    assert r.status_code == 200
    assert (await client.get("/api/profiles/merge-suggestions", headers=admin)).json() == []
    assert (await client.get("/api/profiles", headers=admin)).json()["total"] == 1


async def test_test_chat_profiles_are_hidden_from_the_list(client, admin, maker):  # noqa: F811
    async with maker() as s:
        await handle_message(s, mk_msg(uid="test:abc", text="hi", mid="t1"))
        s.add(Profile(name="Walk-in guest"))  # created by staff, no chat yet: still listed
        await s.commit()
    assert [p["name"] for p in (await client.get("/api/profiles", headers=admin)).json()["items"]] == ["Walk-in guest"]
    assert (await client.get("/api/profiles", headers=admin, params={"include_test": True})).json()["total"] == 2
