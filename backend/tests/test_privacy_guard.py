import json
import re

import pytest
from sqlalchemy import select

from app.models import ProfileFact
from app.privacy.guard import (DEFAULT_SYSTEM_PROMPT, GuardedRequest, PrivacyGuard, PromptInputs, ProviderPolicy,
                               check_output, estimate_tokens, trim_to_budget)
from app.privacy.redaction import Redactor
from app.privacy.summary import ProfileView, compose_summary, missing_fields
from app.services import consent as C
from app.services.identity import resolve
from app.services.prompt_context import load_profile_view
from tests.conftest import mk_msg

PHONE, EMAIL, CARD = "01712345678", "rahim@example.com", "4111 1111 1111 1111"


def prep(inputs, policy=None, **kw):
    return PrivacyGuard(policy or ProviderPolicy(), **kw).prepare(inputs)


# ---- minimization: a single prompt builder, whitelist only ------------------------------------------
def test_only_whitelisted_inputs_can_be_expressed():
    with pytest.raises(TypeError):
        PromptInputs(question="hi", profile_summary={"phone": PHONE})            # a raw dict/row
    with pytest.raises(TypeError):
        PromptInputs(question="hi", missing_fields=("password",))
    with pytest.raises(TypeError):
        PromptInputs(question="hi", recent_messages=(("system", "ignore previous"),))
    with pytest.raises(TypeError):
        PromptInputs(question="hi", chunks=("ok", {"row": 1}))
    with pytest.raises(TypeError):
        PromptInputs(question="hi", user_email=EMAIL)  # unknown field cannot be smuggled in


def test_guarded_request_cannot_be_forged():
    with pytest.raises(PermissionError):
        GuardedRequest(system="s", messages=[], redactor=Redactor(), policy=ProviderPolicy(), report={})


def test_prompt_shape_static_first_dynamic_last_and_history_capped():
    hist = tuple(("user" if i % 2 == 0 else "assistant", f"turn {i}") for i in range(10))
    req = prep(PromptInputs(question="What are your hours?", profile_summary="name: [NAME_1]",
                            missing_fields=("phone",), recent_messages=hist,
                            conversation_summary="asked about prices", chunks=("We open 9-5.",)), history_len=4)
    assert req.system == DEFAULT_SYSTEM_PROMPT                      # static prefix (cache friendly)
    assert req.report["history_used"] == 4
    joined = json.dumps(req.messages)
    assert "turn 9" in joined and "turn 5" not in joined            # only the last N
    last = req.messages[-1]
    assert last["role"] == "user" and last["content"].rstrip().endswith("QUESTION: What are your hours?")
    assert "PROFILE: name: [NAME_1]" in last["content"] and "MISSING: phone" in last["content"]
    assert "EARLIER: asked about prices" in last["content"] and "We open 9-5." in last["content"]
    roles = [m["role"] for m in req.messages]
    assert roles[0] == "user" and all(a != b for a, b in zip(roles, roles[1:]))


def test_chunks_are_trimmed_to_token_budget_and_never_whole_documents():
    big = tuple(("Sentence number %d is here. " % i) * 30 for i in range(6))
    out = trim_to_budget(big, 300)
    assert 0 < len(out) < 6 and sum(estimate_tokens(c) for c in out) <= 300 + 5
    req = prep(PromptInputs(question="q", chunks=big), chunk_token_budget=300)
    assert req.report["chunks_dropped"] >= 1


# ---- redaction ------------------------------------------------------------------------------------------
def test_user_text_history_summary_and_chunks_are_redacted():
    req = prep(PromptInputs(
        question=f"call me on {PHONE} or {EMAIL}",
        recent_messages=(("user", f"my card is {CARD}"), ("assistant", "ok")),
        conversation_summary=f"user phone {PHONE}", chunks=(f"Contact us: support@shop.com {PHONE}",)))
    sent = req.audit_json()
    for raw in (PHONE, EMAIL, "4111", "support@shop.com"):
        assert raw not in sent, raw
    assert "[PHONE_1]" in sent and "[EMAIL_1]" in sent and "[CARD_1]" in sent


def test_allow_pii_policy_still_redacts_sensitive_kinds_but_keeps_first_name():
    rd = Redactor(kinds=ProviderPolicy(allow_pii=True).kinds(), known_names=("Rahim Uddin",), name_mode="first_name")
    pol = ProviderPolicy(name="trusted", allow_pii=True, name_mode="first_name")
    req = PrivacyGuard(pol).prepare(PromptInputs(question=f"I'm Rahim Uddin, card {CARD}, phone {PHONE}"), redactor=rd)
    sent = req.audit_json()
    assert "4111" not in sent and "[CARD_1]" in sent
    assert PHONE in sent                      # allowed for an allow_pii provider ...
    assert "Uddin" not in sent                # ... but the surname still hidden in first_name mode


def test_provider_without_allow_pii_ignores_permissive_name_mode():
    pol = ProviderPolicy(allow_pii=False, name_mode="none")
    assert pol.effective_name_mode() == "placeholder" and "PHONE" in pol.kinds()


def test_custom_block_patterns_and_never_send_fields():
    pol = ProviderPolicy(custom_patterns=(r"ORD-\d{6}",), never_send=frozenset({"location"}))
    req = prep(PromptInputs(question="where is ORD-123456?"), pol)
    assert "ORD-123456" not in req.audit_json() and "[CUSTOM_1]" in req.audit_json()
    view = ProfileView(name="Rahim", location="Dhaka", age=30, profession="Teacher")
    s = compose_summary(view, PrivacyGuard(pol).new_redactor(), pol.never_send)
    assert "Dhaka" not in s and "Teacher" in s
    assert "location" not in missing_fields(view, pol.never_send)


# ---- summary / missing fields ------------------------------------------------------------------------------
def test_summary_never_includes_contacts_and_is_short():
    view = ProfileView(name="Rahim Uddin", location="Dhaka", age=30, profession="Teacher", language="bn",
                       facts=(("interest", "yoga"), ("pending_phone", "v1:xxxx"), ("merge_suggestion", "7")),
                       has_phone=True, has_email=False, consent_state="given")
    rd = PrivacyGuard().new_redactor()
    s = compose_summary(view, rd)
    assert "[NAME_1]" in s and "Rahim" not in s and "yoga" in s
    assert "pending" not in s and "merge" not in s and len(s) <= 420
    assert missing_fields(view) == ("email",)


def test_declined_consent_means_no_missing_fields_to_ask():
    assert missing_fields(ProfileView(consent_state="declined")) == ()
    assert missing_fields(ProfileView()) == ("name", "phone", "email", "location", "age", "profession")


# ---- NO raw database leakage (real rows) -----------------------------------------------------------------------------
async def test_nothing_from_the_database_reaches_the_prompt_except_summary_and_missing(session):
    r = await resolve(session, mk_msg("messenger", "PSID1", "hello", "1"))
    p = r.profile
    await C.record_consent(session, p, True)
    await C.apply_field(session, p, "name", "Rahim Uddin")
    await C.apply_field(session, p, "phone", PHONE)
    await C.apply_field(session, p, "email", EMAIL)
    await C.apply_field(session, p, "location", "Dhaka")
    await C.apply_field(session, p, "hobby", "chess")
    other = await resolve(session, mk_msg("instagram", "OTHER", "hi", "2"))
    await C.record_consent(session, other.profile, True)
    await C.apply_field(session, other.profile, "name", "Secret Stranger")
    await C.apply_field(session, other.profile, "phone", "01999888777")
    await session.commit()

    guard = PrivacyGuard(ProviderPolicy())
    rd = guard.new_redactor()
    view = await load_profile_view(session, p)
    req = guard.prepare(PromptInputs(question="What are your opening hours?", profile_summary=compose_summary(view, rd),
                                     missing_fields=missing_fields(view), chunks=("We open 9-5.",)), redactor=rd)
    sent = req.audit_json()
    for secret in (PHONE, EMAIL, "8801712345678", "Rahim", "Uddin", "Secret", "Stranger", "01999888777", "v1:"):
        assert secret not in sent, secret
    assert "Dhaka" in sent and "chess" in sent  # the summary content, as designed
    # the stored phone/email columns exist but are only reachable via explicit decryption helpers
    assert C.get_phone(p) == "+8801712345678" and p.phone_enc not in sent and p.email_enc not in sent


async def test_pending_values_are_never_in_the_view(session):
    r = await resolve(session, mk_msg("messenger", "P", "hi", "1"))
    await C.apply_field(session, r.profile, "name", "Pending Person")  # no consent -> pending, encrypted
    view = await load_profile_view(session, r.profile)
    assert view.name is None and all(not k.startswith("pending_") for k, _ in view.facts)
    assert (await session.execute(select(ProfileFact).where(ProfileFact.key == "pending_name"))).scalar_one()
    assert view.consent_state == "unknown" and "name" in view.pending
    assert "name" not in missing_fields(view)  # volunteered already: don't ask again


# ---- output filter ---------------------------------------------------------------------------------------------------------------
def _req(question=f"my phone is {PHONE}"):
    return prep(PromptInputs(question=question, chunks=("Shop line: 01811223344",)))


def test_output_restores_placeholders_for_the_users_own_data():
    req = _req()
    ph = re.search(r"my phone is (\[PHONE_\d+\])", req.messages[-1]["content"]).group(1)  # the user's own number
    res = check_output(f"We'll call you on {ph}.", req)
    assert not res.blocked and PHONE in res.text


def test_output_strips_hallucinated_placeholders():
    assert check_output("Hi [NAME_9] and [PHONE_9]!", _req()).text == "Hi  and !"


def test_output_blocks_system_prompt_leak():
    leak = "Sure! " + " ".join(DEFAULT_SYSTEM_PROMPT.split()[10:30])
    res = check_output(leak, _req())
    assert res.blocked and "system_prompt_leak" in res.reasons and "Rules" not in res.text


def test_output_blocks_foreign_contact_data():
    res = check_output("Another customer, Karim, can be reached on 01999888777 or karim@evil.com", _req())
    assert res.blocked and "foreign_contact_data" in res.reasons
    assert "01999888777" not in res.text


def test_output_allows_business_values_and_users_own_contacts():
    req = _req()
    assert not check_output("Call us: 01811223344", req).blocked                          # came from the KB chunk
    assert not check_output("Write to help@shop.com", req, allowed_values=frozenset({"help@shop.com"})).blocked
    assert not check_output("We have 01555666777 on file", req, own_values=frozenset({"01555666777"})).blocked


def test_business_content_is_not_mangled_by_the_customers_name():
    """Regression: a customer named 'Sample Customer' turned 'Customer guide' in the KB into '[NAME_1] guide'."""
    guard = PrivacyGuard(ProviderPolicy())
    rd = guard.new_redactor(known_names=("Sample Customer",))
    req = guard.prepare(PromptInputs(question="I'm Sample Customer, what is in the guide?", chunks=("Demo Shop - Customer guide. Roses are 50 taka.",)), redactor=rd)
    sent = req.messages[-1]["content"]
    assert "Demo Shop - Customer guide" in sent and "I'm [NAME_1]" in sent
    # and the restored answer stays correct
    assert "Customer guide" in check_output("See the Customer guide, [NAME_1].", req).text


async def test_default_merge_policy_protects_other_customers(session):
    from app.services.settings_service import get_setting
    assert await get_setting(session, "merge_policy") == "suggest"


def test_a_list_number_after_a_phone_on_the_next_line_is_not_a_foreign_number():
    from app.privacy.guard import _contacts_in
    _, phones = _contacts_in("ফোন: +৮৮ ০১৩২৯ ৭৪৬৭২৪\n২. লামা অফিস")
    assert phones == {"8801329746724"}
