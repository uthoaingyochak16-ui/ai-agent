import json

import pytest
from sqlalchemy import select

from app.llm.service import LLMService
from app.models import Conversation, Document, Message, Profile
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder
from app.services import consent as C
from app.services import lead as lead_svc
from app.services.ingest import handle_message
from app.services.settings_service import DEFAULTS, set_setting
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM

EMB = (HashingEmbedder(1024), None)
COURSE = "The meditation course runs for four days. Registration for the meditation course is open at every branch."
_LBL = {"name": "নাম", "phone": "ফোন নম্বর"}
_EX = {"name": "রহিম উদ্দিন", "phone": "01XXXXXXXXX"}


def _fill(key, fs):
    names = _LBL[fs[0]] if len(fs) == 1 else ", ".join(_LBL[f] for f in fs[:-1]) + " ও " + _LBL[fs[-1]]
    return DEFAULTS[key].replace("{fields}", names).replace("{example}", ", ".join(_EX[f] for f in fs))


EXPLAIN = _fill("lead_explain", ["name", "phone"])
ASK_WORDS = DEFAULTS["lead_prompt"][:25]
ANSWER = "The meditation course runs for four days."


def extraction(reply: dict):
    return lambda req: json.dumps(reply)


@pytest.fixture
async def env(session, maker, tmp_path):
    async def make(extract: dict | None = None, *, extraction_route=True):
        routes = {"answer": ("anthropic", "small", None, None)}
        if extraction_route:
            routes["extraction"] = ("anthropic", "ext", None, None)
        await seed_llm(session, routes=routes)
        for k, v in (("retrieval_min_similarity", 0.05), ("escalate_below", 0.0), ("extraction_every_n_messages", 0),
                     ("lead_capture_enabled", True)):
            await set_setting(session, k, v)
        st = LocalStorage(str(tmp_path))
        d = Document(title="Course", file_path=st.save("c.txt", COURSE.encode()), type="txt")
        session.add(d)
        await session.commit()
        await process_document(session, d.id, storage=st, embedder=EMB)
        fake = FakeLLM({"small": "The meditation course runs for four days.",
                        "ext": extraction(extract or {"name": None, "location": None})})
        return fake, LLMService(maker, fake)
    return make


async def send(session, llm, text, mid, uid="test:lead"):
    return await handle_message(session, mk_msg(uid=uid, text=text, mid=mid), llm=llm)


async def bot(session):
    return [m.content_redacted for m in (await session.execute(
        select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars()]


async def profile(session) -> Profile:
    p = (await session.execute(select(Profile))).scalar_one()
    await session.refresh(p)
    return p


async def asked(session, llm, text, mid, uid="test:lead"):
    """The guest's message, then the details are asked for in the flow (as create_lead does when they want to join)."""
    r = await send(session, llm, text, mid, uid)
    lead_svc.mark_asked(await session.get(Profile, r.profile.id), await session.get(Conversation, r.conversation.id))
    await session.commit()
    return r


async def test_never_asks_on_its_own(session, env):
    _, llm = await env()
    await send(session, llm, "hi", "c1")
    for i, q in enumerate(("meditation course koto din?", "How long is the meditation course?", "course fee?")):
        r = await send(session, llm, q, f"c2{i}")
        assert (await bot(session))[-1] == ANSWER                             # just the answer: the chat stays on topic
    conv = await session.get(Conversation, r.conversation.id)
    assert conv.lead_awaiting_at is None and conv.lead_pending_question is None
    assert (await profile(session)).personal_info_request_status == "not_asked"


async def test_details_given_after_a_request_are_saved_once(session, env):
    fake, llm = await env({"name": "Rahim", "location": "Mirpur, Dhaka"})
    await asked(session, llm, "meditation course e bhorti hote chai", "a1")
    await send(session, llm, "Rahim, 01712345678, Mirpur Dhaka", "a2")
    reply = (await bot(session))[-1]
    assert reply == DEFAULTS["lead_complete"]                                  # thanks + anything else?
    p = await profile(session)
    assert (p.name, p.location, C.get_phone(p), p.consent_given) == ("Rahim", "Mirpur, Dhaka", "+8801712345678", True)
    assert p.personal_info_request_status == "provided"
    assert "সংরক্ষণ করতে পারি" not in reply                                   # no separate consent question

    await send(session, llm, "registration kothay?", "a3")
    assert (await bot(session))[-1] == ANSWER                                  # never asked again


async def test_partial_details_get_no_reminder(session, env):
    fake, llm = await env({"name": "Karim", "location": None})
    await asked(session, llm, "meditation course koto din?", "b1")
    await send(session, llm, "amar nam Karim", "b2")
    reply = (await bot(session))[-1]
    assert reply == DEFAULTS["lead_thanks"] and ASK_WORDS not in reply
    p = await profile(session)
    assert p.name == "Karim" and p.personal_info_request_status == "provided"
    fake.script["ext"] = extraction({"name": None, "location": None})
    for i, q in enumerate(("registration kothay?", "course fee?", "course er schedule ki?")):
        await send(session, llm, q, f"b3{i}")
        assert "ফোন নম্বর" not in (await bot(session))[-1]                     # the phone is never nagged for


async def test_ignored_request_answers_anyway_and_never_reminds(session, env):
    _, llm = await env()
    await asked(session, llm, "meditation course ki?", "d1")
    await send(session, llm, "koto din chole?", "d2")
    reply = (await bot(session))[-1]
    assert "four days" in reply and ASK_WORDS not in reply
    p = await profile(session)
    assert p.personal_info_request_status == "deferred" and not p.consent_given


async def test_refusal_is_accepted_politely(session, env):
    _, llm = await env({"name": None, "location": None, "reply": "refused"})
    await asked(session, llm, "meditation course koto din?", "x1")
    await send(session, llm, "na, dite chai na", "x2")
    reply = (await bot(session))[-1]
    assert reply == DEFAULTS["lead_declined_ack"]
    assert (await profile(session)).personal_info_request_status == "declined"


async def test_details_already_known_are_not_asked(session, env):
    _, llm = await env()
    await send(session, llm, "hello", "e0", uid="test:known")
    p = await profile(session)
    p.name = "Rina"
    C.set_phone(p, "+8801712345678")
    await session.commit()
    await send(session, llm, "meditation course koto din?", "e1", uid="test:known")
    assert "four days" in (await bot(session))[-1]


async def test_without_an_extraction_route_a_comma_list_still_works(session, env):
    _, llm = await env(extraction_route=False)
    await asked(session, llm, "meditation course?", "f1")
    await send(session, llm, "Salma, 01812345678, Uttara", "f2")
    p = await profile(session)
    assert (p.name, p.location, C.get_phone(p)) == ("Salma", "Uttara", "+8801812345678")


async def test_off_by_default(session, env):
    _, llm = await env()
    await set_setting(session, "lead_capture_enabled", False)
    await session.commit()
    await send(session, llm, "how long is the meditation course?", "g1")
    assert "four days" in (await bot(session))[-1]


async def test_phone_without_country_code_is_saved_too(session, env):
    _, llm = await env({"name": None, "location": None})
    await asked(session, llm, "meditation course koto din?", "r1")
    await send(session, llm, "০১৮১২৩৪৫৬৭৯", "r2")                              # Bangla digits, no country code
    assert C.get_phone(await profile(session)) == "+8801812345679"


async def test_a_phone_that_passes_the_card_checksum_is_still_saved(session, env):
    _, llm = await env({"name": "Uthoaingyo Chak", "location": None})
    await asked(session, llm, "meditation course koto din?", "p1")
    await send(session, llm, "01812345604,CUET", "p2")                        # +8801812345604: 13 digits, Luhn-valid
    assert C.get_phone(await profile(session)) == "+8801812345604"


async def test_a_phone_already_on_another_profile_still_counts_as_given(session, env):
    _, llm = await env({"name": "Tanvir", "location": "Mirpur"})
    await send(session, llm, "hello", "s0", uid="test:other")
    other = await profile(session)
    C.set_phone(other, "+8801712345678")
    await session.commit()
    await asked(session, llm, "meditation course koto din?", "s1", uid="test:new")
    await send(session, llm, "Tanvir, 01712345678, Mirpur", "s2", uid="test:new")   # merge suggested, not saved here
    assert (await bot(session))[-1].endswith(DEFAULTS["lead_anything_else"])


def test_asks_about_data():
    for q in ("tumi ki amar data save korcho?", "আমার তথ্য কি সংরক্ষণ করছেন?", "Are you storing my data?",
              "keno amar number lagbe?", "আমার তথ্য কেন লাগবে?"):
        assert lead_svc.asks_about_data(q), q
    for q in ("course fee koto?", "Rahim, 01712345678, Dhaka", "amar data muche din"):
        assert not lead_svc.asks_about_data(q), q


async def test_never_says_saved_but_answers_when_asked(session, env):
    fake, llm = await env({"name": "Rahim", "location": "Mirpur, Dhaka", "reply": "details"})
    await asked(session, llm, "meditation course koto din?", "t1")
    await send(session, llm, "Rahim, 01712345678, Mirpur Dhaka", "t2")
    for text in await bot(session):
        assert "সংরক্ষণ" not in text and "saved" not in text.lower()
    await send(session, llm, "tumi ki amar data save korcho?", "t3")
    assert (await bot(session))[-1] == DEFAULTS["data_saving_reply"]          # details complete: just yes + why


async def test_confused_guest_gets_the_reason_once(session, env):
    fake, llm = await env({"name": None, "location": None, "reply": "confused"})
    await asked(session, llm, "meditation course koto din?", "u1")
    await send(session, llm, "ki likhbo bujhi nai", "u2")
    assert (await bot(session))[-1] == EXPLAIN                                # why, once; still waiting
    await send(session, llm, "keno lagbe bujhlam na", "u3")                   # confused again: no second explanation
    assert (await bot(session))[-1] == DEFAULTS["lead_declined_ack"]           # the question was answered already
    assert (await profile(session)).personal_info_request_status == "deferred"


async def test_asking_why_while_we_wait_says_yes_and_asks_again(session, env):
    fake, llm = await env({"name": None, "location": None, "reply": "other"})
    await asked(session, llm, "meditation course koto din?", "v1")
    await send(session, llm, "keno amar tottho lagbe?", "v2")
    assert (await bot(session))[-2:] == [DEFAULTS["data_saving_reply"], EXPLAIN]  # long: 2 messages


@pytest.mark.parametrize("text,name,location", [
    ("amar  name রহিম উদ্দিন, phone 01812345679, মিরপুর, ঢাকা", "রহিম উদ্দিন", "মিরপুর, ঢাকা"),
    ("নাম: করিম হাসান\nফোন: ০১৯১২৩৪৫৬৭০\nঠিকানা: আগ্রাবাদ, চট্টগ্রাম", "করিম হাসান", "আগ্রাবাদ, চট্টগ্রাম"),
    ("Ami Tanvir Ahmed. Thaki Uttara sector 7 e. Number +880 1712-345670", "Tanvir Ahmed", "Uttara sector 7"),
    ("my name is Sadia Islam and I live in Sylhet, call me on 8801612345678", "Sadia Islam", "Sylhet"),
    ("01512345670 eta amar number, ami Nusrat, Rajshahi theke", "Nusrat", "Rajshahi"),
    ("Name - Md. Abdul Kuddus; Address - Vill: Kalia, Narail; Mob: 01712 345671", "Md. Abdul Kuddus", "Vill: Kalia, Narail"),
    ("আমি রাশেদ, বাসা মোহাম্মদপুর ঢাকায়। মোবাইল নম্বর ০১৮১২-৩৪৫৬৭৮", "রাশেদ", "মোহাম্মদপুর ঢাকায়"),
    ("phone 01812345679 r nam Mim, Cumilla", "Mim", "Cumilla"),
    ("amar ammur nam Rahima, kintu amar nam Fahim, thaki Khulna, 01912345672", "Fahim", "Khulna"),  # not the mother
    ("Rahim, 01712345678, Mirpur Dhaka", "Rahim", "Mirpur Dhaka"),
])
def test_fallback_reads_messy_details(text, name, location):
    assert lead_svc._fallback_split(text) == {"name": name, "location": location}


@pytest.mark.parametrize("text", ["ami jante chai", "na thak", "keno lagbe?", "ami course e bhorti hote chai"])
def test_fallback_does_not_mistake_a_sentence_for_a_name(text):
    assert lead_svc._fallback_split(text) == {}


async def test_a_clearly_labelled_value_fills_what_the_ai_missed(session, env):
    _, llm = await env({"name": None, "location": "Mirpur", "reply": "details"})   # the AI drops the name
    await asked(session, llm, "meditation course koto din?", "w1")
    await send(session, llm, "নাম: করিম হাসান, ফোন: 01812345679, Mirpur", "w2")
    assert (await profile(session)).name == "করিম হাসান"
