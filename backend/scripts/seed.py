"""Seed sample data (idempotent):  python -m scripts.seed [--admin-email you@x.com --admin-password '...']

Creates: variables, Q&A pairs (greetings, hours, delivery...), one sample document, one sample profile,
provider rows (disabled until you add keys), model price rows (UNVERIFIED, set real prices), and the four routes.
Nothing here contains real customer data.
"""
import argparse
import asyncio
import tempfile

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models import (AdminUser, Document, LLMModel, LLMProviderConfig, LLMRoute, Profile, ProfileFact, QAPair, Variable)
from app.rag.documents import get_storage, process_document
from app.rag.embeddings import embed_texts
from app.rag.templates import normalize_question
from app.services import consent as C
from app.services.settings_service import get_setting, set_setting

VARIABLES = {"shop_name": "Demo Shop", "open_hours": "9 am to 8 pm, Saturday to Thursday", "support_phone": "01800000000",
             "delivery_fee_dhaka": "60 taka", "delivery_fee_outside": "120 taka"}

QA = [
    ("Hi", "Hello! Welcome to {shop_name}. How can I help you today?", "greeting"),
    ("Hello", "Hello! Welcome to {shop_name}. How can I help you today?", "greeting"),
    ("Assalamu alaikum", "Wa alaikum assalam! Welcome to {shop_name}. How can I help you?", "greeting"),
    ("হ্যালো", "হ্যালো! {shop_name}-এ স্বাগতম। আমি কীভাবে সাহায্য করতে পারি?", "greeting"),
    ("Thanks", "You're welcome! Let us know if you need anything else.", "greeting"),
    ("ধন্যবাদ", "আপনাকেও ধন্যবাদ! আর কিছু জানতে চাইলে জিজ্ঞেস করুন।", "greeting"),
    ("What are your opening hours?", "We are open {open_hours}.", "info"),
    ("আপনাদের দোকান কখন খোলা থাকে?", "আমরা খোলা থাকি {open_hours}।", "info"),
    ("What is the delivery charge?", "Delivery costs {delivery_fee_dhaka} inside Dhaka and {delivery_fee_outside} outside Dhaka.", "delivery"),
    ("What is your phone number?", "You can call us on {support_phone}.", "info"),
]

DOC = """Demo Shop - Customer guide

Delivery. Orders inside Dhaka are delivered in one to two working days. Orders outside Dhaka take three to five working days.
Delivery is free for orders above 3000 taka.

Returns and refunds. You can return an unused item within seven days of receiving it if you keep the receipt.
Refunds go back to the original payment method within five to seven working days. Damaged items are replaced free of charge.

Warranty. All electronic items carry a one year warranty against manufacturing defects. Water damage and physical damage are not covered.

Payment. We accept cash on delivery, bKash, Nagad and card payments. For bKash payments send the amount to our merchant number and share the transaction ID.
"""

ROUTES = {  # task -> (provider, model, max_tokens). Claude by default; OpenAI / Gemini can be picked per task in AI settings.
    "answer": ("anthropic", "claude-sonnet-5-5", 600),
    "answer_large": ("anthropic", "claude-sonnet-5-5", 800),
    "router": ("anthropic", "claude-haiku-5-5", 400),
    "rewrite": ("anthropic", "claude-haiku-5-5", 200),
    "keywords": ("anthropic", "claude-haiku-5-5", 300),
    "keygen": ("anthropic", "claude-haiku-5-5", 300),
    "extraction": ("anthropic", "claude-haiku-5-5", 400),
    "summarization": ("anthropic", "claude-haiku-5-5", 300),
}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email")
    ap.add_argument("--admin-password")
    a = ap.parse_args()
    async with get_sessionmaker()() as s:
        if a.admin_email and a.admin_password:
            if not (await s.execute(select(AdminUser).where(AdminUser.email == a.admin_email.lower()))).scalar_one_or_none():
                s.add(AdminUser(email=a.admin_email.lower(), name="Admin", role="admin", password_hash=hash_password(a.admin_password)))
        for k, v in VARIABLES.items():
            if await s.get(Variable, k) is None:
                s.add(Variable(key=k, value=v))
        existing = {q.question_norm for q in (await s.execute(select(QAPair))).scalars()}
        todo = [q for q in QA if normalize_question(q[0]) not in existing]
        if todo:
            vecs = await embed_texts(s, [q[0] for q in todo])
            for (q, ans, cat), vec in zip(todo, vecs):
                s.add(QAPair(question=q, question_norm=normalize_question(q), answer_template=ans, category=cat, embedding=vec))
        for name in ("anthropic", "openai", "google"):
            if not (await s.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == name))).scalar_one_or_none():
                s.add(LLMProviderConfig(name=name, enabled=False))
        for task, (prov, model, mt) in ROUTES.items():
            if not (await s.execute(select(LLMRoute).where(LLMRoute.task == task))).scalar_one_or_none():
                s.add(LLMRoute(task=task, primary_provider=prov, primary_model=model, max_tokens=mt, temperature=0.2))
            if not (await s.execute(select(LLMModel).where(LLMModel.provider == prov, LLMModel.model == model))).scalar_one_or_none():
                s.add(LLMModel(provider=prov, model=model, input_price=0, output_price=0, price_verified=False))
        await s.commit()

        if not (await s.execute(select(Document).where(Document.title == "Demo Shop guide"))).scalar_one_or_none():
            st = get_storage()
            d = Document(title="Demo Shop guide", file_path=st.save("guide.txt", DOC.encode()), type="txt")
            s.add(d)
            await s.commit()
            print("document:", await process_document(s, d.id))
        if not (await s.execute(select(Profile).where(Profile.name == "Sample Customer"))).scalar_one_or_none():
            p = Profile()
            s.add(p)
            await s.flush()
            await C.record_consent(s, p, True)
            for k, v in (("name", "Sample Customer"), ("location", "Dhaka"), ("profession", "Teacher"), ("language", "bn"), ("phone", "01712345678"), ("email", "sample@example.com")):
                await C.apply_field(s, p, k, v)
            s.add(ProfileFact(profile_id=p.id, key="interest", value="home appliances"))
        if await get_setting(s, "embedding_provider") == "hash":
            print("note: embedding provider is 'hash' (offline test mode). Set openai/google in AI settings for real semantic search.")
        await s.commit()
    print("seeded. Add API keys under AI settings and set real model prices before going live.")


asyncio.run(main())
