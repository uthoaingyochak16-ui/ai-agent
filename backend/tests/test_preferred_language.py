from sqlalchemy import select

from app.models import Profile
from app.services.ingest import handle_message
from app.services.language import style
from tests.conftest import mk_msg


def test_style():
    assert style("রক্তদান কোথায় করা যায়?") == "bn"
    assert style("course fee koto?") == "banglish"
    assert style("What is the course fee?") == "en"
    for unclear in ("hello", "ok", "[PHONE_1],CUET", "Rahim Uddin", ""):
        assert style(unclear) is None


_mid = iter(range(10_000))


async def send_all(session, texts, uid="test:lang"):
    for t in texts:
        i = next(_mid)
        await handle_message(session, mk_msg(uid=uid, text=t, mid=f"{uid}-{i}"), reply=False)


async def language(session):
    p = (await session.execute(select(Profile))).scalar_one()
    await session.refresh(p)
    return p.language


async def test_set_after_three_real_messages(session):
    await send_all(session, ["hello", "course fee koto?", "ok", "kobe shuru hobe?"])
    assert await language(session) is None                                   # only two that tell
    await send_all(session, ["ar schedule ta janaben?"])
    assert await language(session) == "banglish"


async def test_majority_wins_and_follows_a_lasting_switch(session):
    await send_all(session, ["কোর্স ফি কত?", "কবে শুরু হবে?", "What is the schedule please?"])
    assert await language(session) == "bn"
    await send_all(session, [f"Can you tell me about course {i} please?" for i in range(4)])
    assert await language(session) == "en"
