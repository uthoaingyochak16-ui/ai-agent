"""Organise the existing knowledge database (run once after migration 0025; safe to run again).

    python -m scripts.organize_kb            # dry run: prints what it would do
    python -m scripts.organize_kb --apply    # does it

Adds only; never deletes or overwrites data an admin typed:
1. Knowledge base: the document "Quantum Foundation — Offices, Addresses & Contacts", one record (= one chunk) per
   office / branch / lab / service centre. Every value comes from the existing knowledge base (chunk ids in
   `source`); nothing is invented. Values the knowledge base does not have (opening hours, map links) are written as
   "not published". Unclear entries are NOT published: they go to Unknown questions as "needs_review".
2. Variables: short values only. Filled from the knowledge base where it has them; the rest are created empty
   ("needs a value") - an empty variable is never used in an answer.
3. Known Q&A, category "Contact & Address": Bangla, English and Banglish wordings of the common contact questions
   (skipped when the same question already exists).
4. Keyword index rebuilt (chunks, Q&A, variables, programs) + suggested keywords for Unknown questions.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import Document, QAPair, UnansweredQuestion, Variable
from app.rag.documents import LocalStorage, bump_context_version, process_document
from app.rag.embeddings import embed_texts, embedding_signature
from app.rag.keyword_index import rebuild_index, stats
from app.rag.templates import normalize_question
from app.services import learning

DOC_TITLE = "Quantum Foundation — Offices, Addresses & Contacts"
NOT_PUBLISHED = "ওয়েবসাইটে দেওয়া নেই — যাওয়ার আগে ফোন করে নিশ্চিত হয়ে নিন"

# (name, type, address, area, phones, email, services, keywords, source chunk ids)
OFFICES = [
    ("কোয়ান্টাম ফাউন্ডেশন কেন্দ্রীয় দফতর (হেড অফিস) / Central office (Head office)", "কেন্দ্রীয় দফতর / head office",
     "৩১/ভি, (২য় তলা), শিল্পাচার্য জয়নুল আবেদিন সড়ক, শান্তিনগর, (ইস্টার্ন প্লাস মার্কেটের পাশে), ঢাকা-১২১৭", "শান্তিনগর, ঢাকা",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬০৫, +৮৮ ০১৭১৪ ৯৭৪৩৩৩; ফোন: +৮৮ ০২ ২২২২২১৪৪১, +৮৮ ০২ ৪৮৩১৯৩৭৭", "info@quantummethod.org.bd",
     "সাধারণ তথ্য, কোর্স ও রেজিস্ট্রেশন, অন্যান্য যোগাযোগ",
     "হেড অফিস, কেন্দ্রীয় দফতর, প্রধান কার্যালয়, head office, central office, main office, shantinagar, শান্তিনগর", [4142, 4143]),
    ("স্বাগতায়ন (তথ্য ও অভ্যর্থনা) / Shagatayan (information desk)", "তথ্যকেন্দ্র / information centre",
     "১/১, পায়োনিয়ার রোড, ওয়াইএমসিএ ভবন, (নিচতলা), সেগুনবাগিচা, কাকরাইল, ঢাকা-১০০০", "কাকরাইল, ঢাকা",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬১০, +৮৮ ০১৭১১ ৬৭১৮৫৮", "shagatayan@quantummethod.org.bd",
     "যে-কোনো তথ্য, ইয়োগা ওরিয়েন্টেশন", "স্বাগতায়ন, shagatayan, kakrail, কাকরাইল, ymca, information, তথ্য", [4145, 4208]),
    ("স্বেচ্ছা রক্তদান কার্যক্রম ও কোয়ান্টাম ল্যাব / Blood donation & Quantum Lab", "ল্যাব ও রক্তদান কেন্দ্র / lab",
     "৩১/ভি, (নিচতলা), শিল্পাচার্য জয়নুল আবেদিন সড়ক, (ইস্টার্ন প্লাস মার্কেটের পাশে), শান্তিনগর, ঢাকা-১২১৭", "শান্তিনগর, ঢাকা",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৭৭, +৮৮ ০১৩২৯ ৭৪৬৬৯৯, +৮৮ ০১৭১৪ ০১০৮৬৯; ফোন: +৮৮ ০২ ২২২২২১৮৬৯, +৮৮ ০২ ৪৮৩২২৮০৯",
     "blood@quantummethod.org.bd", "রক্তদান, রক্ত সংগ্রহ, ক্রসম্যাচ, ল্যাব টেস্ট",
     "রক্তদান, রক্ত, ব্লাড, blood, blood donation, blood bank, rokto, rokto dan, quantum lab, ল্যাব, lab", [4218, 4219]),
    ("দাফন সেবা কার্যক্রম / Dafon (burial) service", "সেবা কেন্দ্র / service centre",
     "১/১, পায়োনিয়ার রোড, ওয়াইএমসিএ ভবন, (নিচতলা), সেগুনবাগিচা, কাকরাইল, ঢাকা-১০০০", "কাকরাইল, ঢাকা",
     "মোবাইল: +৮৮ ০১৩১৩ ৪৮৬৫০৯, +৮৮ ০১৩১৩ ৪৮৬৫১৫", "dafon@quantummethod.org.bd", "দাফন সেবা",
     "দাফন, dafon, burial, kafon, কাফন", [4230]),
    ("স্বেচ্ছা খতনা কার্যক্রম ও স্বাস্থ্যসেবা কেন্দ্র / Khatna & health service centre", "সেবা কেন্দ্র / service centre",
     "বাড়ি#১, রোড#১, এভিনিউ#১, সেকশন#১১, মিরপুর, ঢাকা-১২১৬", "মিরপুর, ঢাকা", "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৪২", None,
     "খতনা, স্বাস্থ্যসেবা", "খতনা, khatna, স্বাস্থ্যসেবা, health, mirpur 11", [4234]),
    ("চট্টগ্রাম সেন্টার / Chattogram Centre", "সেন্টার / centre",
     "'বদরে মহল', বাসা#৬৬, (নিচতলা), রোড#৬, ও আর নিজাম রোড আ/এ, চট্টগ্রাম-৪২০৩", "চট্টগ্রাম",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৭০০, +৮৮ ০১৭১১ ৩৯৩০১০; ফোন: +৮৮ ০২৪১ ৩৫৬০৫৪", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম",
     "চট্টগ্রাম, chattogram, chittagong, ctg", [4149]),
    ("সিলেট সেন্টার / Sylhet Centre", "সেন্টার / centre",
     "'নেহার গার্ডেন', স্বপ্নীল#৫২, (২য় তলা) রামের দিঘীর পাড়, মির্জাজাঙ্গাল, সিলেট-৩১০০", "সিলেট",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৮১, +৮৮ ০১৭১৩ ৩০১৫২২", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম", "সিলেট, sylhet", [4150]),
    ("রাজশাহী সেন্টার / Rajshahi Centre", "সেন্টার / centre",
     "৫৩৪, শেরশাহ রোড, (লক্ষ্মীপুর মোড় থেকে পশ্চিমে ডায়াবেটিস হাসপাতালের পূর্ব পাশে), লক্ষ্মীপুর, রাজশাহী-৬০০০", "রাজশাহী",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৬০, +৮৮ ০১৯১৪ ৯৯৯৪৪৬", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম", "রাজশাহী, rajshahi", [4151]),
    ("খুলনা শাখা / Khulna Branch", "শাখা / branch",
     "১৯, শিরিশনগর, আকবরাবাদ এস্টেট, খানজাহান আলী রোড, (বেক্সিমকো অফিসের পাশে), খুলনা-৯১০০", "খুলনা",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৭৪, +৮৮ ০১৭৪০ ৯৩৯৯৯৯, +৮৮ ০১৯২৩ ৯৯৫৫১১", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম",
     "খুলনা, khulna", [4152]),
    ("বরিশাল শাখা / Barishal Branch", "শাখা / branch", "হদুয়া লজ, ২৫১, জর্ডন রোড, বরিশাল-৮২০০", "বরিশাল",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৭৯", "barishal@quantummethod.org.bd", "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম",
     "বরিশাল, barishal, barisal", [4153]),
    ("রংপুর শাখা / Rangpur Branch", "শাখা / branch",
     "বাসা নং#১, রোড নং#৪, (ভূমি অফিস এবং রাধাবল্লভ মসজিদের মাঝে), রাধাবল্লভ, রংপুর-৫৪০০", "রংপুর",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৬৭, +৮৮ ০১৭৮৩ ০১৫১০১", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম", "রংপুর, rangpur", [4154]),
    ("ময়মনসিংহ শাখা / Mymensingh Branch", "শাখা / branch",
     "মুমিনুন্নেসা এক্সেল টাওয়ার, ২য় তলা, ফ্ল্যাট#১(ই জি), ৮নং মনমোহন নিয়োগী রোড, পন্ডিত পাড়া, ময়মনসিংহ-২২০০", "ময়মনসিংহ",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৯৫, +৮৮ ০১৯১২ ৭২৩০৯৫", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম",
     "ময়মনসিংহ, mymensingh, moymonsingh", [4155]),
    ("উত্তরা সেন্টার / Uttara Centre", "সেন্টার / centre", "বাসা#৩৫, (নিচতলা), রোড#১৩, সেক্টর#০৩, উত্তরা, ঢাকা-১২৩০",
     "উত্তরা, ঢাকা", "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৭২৮, +৮৮ ০১৯২৩ ৯৬৮৮৮৮", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম",
     "উত্তরা, uttara", [4156]),
    ("মিরপুর সেন্টার / Mirpur Centre (Mirpur office)", "সেন্টার / centre",
     "বাড়ি#৫/২, ব্লক#ই, রোড#৬, মিরপুর-২, (জার্মান টেকনিক্যালের পিছনে), ঢাকা-১২১৬", "মিরপুর, ঢাকা",
     "মোবাইল: +৮৮ ০১৮৭০ ৭২৫৯২১, +৮৮ ০১৭১৩ ০৮৭০৬৮", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম",
     "মিরপুর, mirpur, mirpur office, mirpur 2", [4157]),
    ("মতিঝিল সেন্টার / Motijheel Centre", "সেন্টার / centre",
     "৯২ আরামবাগ, আল খালিফ টাওয়ার (কাবিলা বিল্ডিং), ২য় তলা, কালভার্ট রোড, আরামবাগ, মতিঝিল, ঢাকা-১০০০", "মতিঝিল, ঢাকা",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৩০, +৮৮ ০১৭৪৪ ৮৮৫৫১১", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম",
     "মতিঝিল, motijheel, arambagh, আরামবাগ", [4158]),
    ("বনানী সেন্টার / Banani Centre", "সেন্টার / centre", "বাড়ি নং #৫৯, (৩য় তলা), রোড নং #১, ব্লক #আই, বনানী, ঢাকা-১২১৩",
     "বনানী, ঢাকা", "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬২৬", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম", "বনানী, banani", [4159]),
    ("বনশ্রী সেন্টার / Banasree Centre", "সেন্টার / centre",
     "বাড়ি নং#২৩, রোড নং#১, ব্লক#এ, (২য় তলা), বনশ্রী আ/এ, রামপুরা, ঢাকা-১২১৯", "বনশ্রী, রামপুরা, ঢাকা",
     "মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬২০", None, "কোর্স, সাদাকায়ন ও শাখার কার্যক্রম", "বনশ্রী, banasree, banashree, rampura", [4159]),
    ("কোয়ান্টামম, লামা / Quantumom, Lama", "কেন্দ্র / campus", "বোধিছড়া, কেয়াজুপাড়া, সরই, লামা, বান্দরবান-৩২৭৪",
     "লামা, বান্দরবান", "মোবাইল: +৮৮ ০১৩১৩ ৪৮৬৫২০, +৮৮ ০১৬০১ ৪৮৬৫২১, +৮৮ ০১৭১৪ ০৯৪২৭১", "quantumom@quantummethod.org.bd",
     "কোয়ান্টামম কার্যক্রম, কসমো স্কুল", "কোয়ান্টামম, quantumom, lama, লামা, bandarban, বান্দরবান", [4261]),
    ("Quantum Meditation Society (UK), London", "আন্তর্জাতিক শাখা / international branch",
     "66 Hanbury Street, London E1 5JL, United Kingdom", "London, UK", "ফোন: +44 203 432 2672", "info@quantumlondon.org.uk",
     "মেডিটেশন কার্যক্রম", "london, লন্ডন, uk, united kingdom, abroad, বিদেশ", [4161]),
    ("ওয়েব সাপোর্ট / Web support", "সেবা যোগাযোগ / service contact", NOT_PUBLISHED, "অনলাইন",
     "মোবাইল: +৮৮ ০১৬১৭ ৮২৬৮৮৬", "webmaster@quantummethod.org.bd", "ওয়েবসাইট ও অনলাইন সমস্যা",
     "ওয়েব সাপোর্ট, web support, website, ওয়েবসাইট, online problem, login", [4144]),
    ("অজেয় ১৯ / Ajeyo 19", "সেবা যোগাযোগ / service contact", NOT_PUBLISHED, "সারা দেশ",
     "ফোন: +৮৮ ০১৭৩০ ৪৪১২৭৩", "ajeo19@quantummethod.org.bd", "তরুণদের রক্তদান কার্যক্রম",
     "অজেয় ১৯, ajeyo 19, ajeo19, youth blood donation", [4226]),
    ("কোয়ান্টাম কসমো স্কুল — ভর্তি ও শিশুর সন্ধান / Cosmo School admission", "সেবা যোগাযোগ / service contact", NOT_PUBLISHED,
     "লামা, বান্দরবান", "ফোন: ০১৩১৩৪৮৬৫৩০", "admission@quantummethod.org.bd", "সুবিধাবঞ্চিত শিশুর ভর্তি",
     "কসমো স্কুল, cosmo school, admission, ভর্তি, শিশু", [4256]),
]

# Not published: the knowledge base is unclear or incomplete. Each becomes an Unknown question for an admin.
NEEDS_REVIEW = [
    ("যশোর শাখার ঠিকানা কী?", "Knowledge base has only phones for the Jessore branch (+৮৮ ০৪২১ ৫১৩৯৮, +৮৮ ০১৩২৯ ৭৪৬৬৫৬, from a "
                             "course notice, chunk 4168); the branch address is not given. Confirm before publishing."),
    ("অফিস কখন খোলা থাকে? (opening hours)", "No opening hours anywhere in the knowledge base. 13 existing Q&As use {office_hours}; "
                                          "set the Variable office_hours and they start answering."),
    ("কোয়ান্টাম ফাউন্ডেশনের মহাপরিচালক কে?", "The knowledge base cites an old seminar report (chunk 4140). Confirm the current name."),
]

# key -> (value from the knowledge base or "" = needs a value, description, source)
VARIABLES = {
    "head_office_phone": ("+৮৮ ০১৭১৪ ৯৭৪৩৩৩, +৮৮ ০১৩২৯ ৭৪৬৬০৫", "কেন্দ্রীয় দফতরের ফোন নম্বর / head office phone number", "chunk 4143"),
    "website_url": ("https://quantummethod.org.bd", "ওয়েবসাইট / website", "chunks 4147, 4148"),
    "registration_link": ("https://yogafoundation.com.bd/bn/forms/course-registration-form",
                          "কোর্স রেজিস্ট্রেশন ফর্মের লিংক / course registration link", "chunk 4169"),
    "course_fee": ("১১,৫০০ টাকা", "কোয়ান্টাম মেথড কোর্সের ফি / course fee", "chunk 4170"),
    "hotline_number": ("", "হটলাইন নম্বর / hotline (not in the knowledge base: needs a value)", None),
    "office_hours": ("", "অফিস সময় / office hours (not in the knowledge base: needs a value)", None),
    "registration_deadline": ("", "রেজিস্ট্রেশনের শেষ তারিখ / registration deadline (needs a value)", None),
}

HEAD_ADDR_BN = "কোয়ান্টাম ফাউন্ডেশনের কেন্দ্রীয় দফতর (হেড অফিস): ৩১/ভি, (২য় তলা), শিল্পাচার্য জয়নুল আবেদিন সড়ক, শান্তিনগর, (ইস্টার্ন প্লাস মার্কেটের পাশে), ঢাকা-১২১৭।"
HEAD_ADDR_EN = "Quantum Foundation central office (head office): 31/V (2nd floor), Shilpacharya Zainul Abedin Sarak, Shantinagar (beside Eastern Plus Market), Dhaka-1217."
LAB_BN = ("স্বেচ্ছা রক্তদান কার্যক্রম ও কোয়ান্টাম ল্যাব — মোবাইল: +৮৮ ০১৩২৯ ৭৪৬৬৭৭, +৮৮ ০১৩২৯ ৭৪৬৬৯৯, +৮৮ ০১৭১৪ ০১০৮৬৯; "
          "ফোন: +৮৮ ০২ ২২২২২১৮৬৯, +৮৮ ০২ ৪৮৩২২৮০৯; ইমেইল: blood@quantummethod.org.bd")
LAB_EN = ("Blood donation & Quantum Lab — mobile: +88 01329 746677, +88 01329 746699, +88 01714 010869; "
          "phone: +88 02 222221869, +88 02 48322809; email: blood@quantummethod.org.bd")
MIRPUR_BN = "মিরপুর সেন্টার: বাড়ি#৫/২, ব্লক#ই, রোড#৬, মিরপুর-২, (জার্মান টেকনিক্যালের পিছনে), ঢাকা-১২১৬। মোবাইল: +৮৮ ০১৮৭০ ৭২৫৯২১, +৮৮ ০১৭১৩ ০৮৭০৬৮।"
MIRPUR_EN = "Mirpur Centre: House 5/2, Block E, Road 6, Mirpur-2 (behind German Technical), Dhaka-1216. Mobile: +88 01870 725921, +88 01713 087068."

# Contact & Address Q&A: Bangla / English / Banglish wordings of the same question (each its own row)
CONTACT_QA = [
    ("কোয়ান্টাম ফাউন্ডেশনের হেড অফিস কোথায়?", HEAD_ADDR_BN),
    ("Quantum Foundation head office kothay?", HEAD_ADDR_BN),
    ("Where is the Quantum Foundation head office?", HEAD_ADDR_EN),
    ("কোয়ান্টামের হেড অফিসের ফোন নম্বর কত?", "কেন্দ্রীয় দফতরের (হেড অফিস) মোবাইল: {head_office_phone}। ইমেইল: info@quantummethod.org.bd"),
    ("Quantum er head office er phone number koto?", "কেন্দ্রীয় দফতরের (হেড অফিস) মোবাইল: {head_office_phone}। ইমেইল: info@quantummethod.org.bd"),
    ("What is the Quantum Foundation head office phone number?", "Head office mobile: {head_office_phone}. Email: info@quantummethod.org.bd"),
    ("কোয়ান্টাম ল্যাবের ফোন নম্বর কত?", LAB_BN),
    ("Quantum Lab er phone number koto?", LAB_BN),
    ("What is the Quantum Lab phone number?", LAB_EN),
    ("মিরপুর অফিসের ঠিকানা কী?", MIRPUR_BN),
    ("Mirpur office address", MIRPUR_EN),
    ("Mirpur office er thikana ki?", MIRPUR_BN),
    ("আপনাদের ওয়েবসাইট কী?", "আমাদের ওয়েবসাইট: {website_url}। সব শাখার ঠিকানা: {website_url}/bn/contacts"),
    ("Apnader website ki?", "আমাদের ওয়েবসাইট: {website_url}। সব শাখার ঠিকানা: {website_url}/bn/contacts"),
    ("What is your website?", "Our website: {website_url}. All branch addresses: {website_url}/bn/contacts"),
]


def contact_records() -> list[dict]:
    out = []
    for name, kind, address, area, phones, email, services, keywords, src in OFFICES:
        body = "\n".join([f"অফিস: {name}", f"ধরন: {kind}", f"ঠিকানা: {address}", f"এলাকা/জেলা: {area}", f"ফোন: {phones}",
                          f"ইমেইল: {email or NOT_PUBLISHED}", f"খোলার সময়: {NOT_PUBLISHED}", f"সেবা: {services}",
                          f"ম্যাপ লিংক: {NOT_PUBLISHED}"])
        out.append({"question": f"{name} — ঠিকানা ও ফোন নম্বর (address, phone, contact, thikana)", "answer": body,
                    "topic": "যোগাযোগ ও ঠিকানা", "category": "Contact & Address",
                    "keywords": f"{keywords}, ঠিকানা, address, thikana, ফোন, phone, number, যোগাযোগ, contact",
                    "source": f"existing knowledge base chunks {src} (website)"})
    return out


async def run(apply: bool) -> dict:
    report: dict = {"apply": apply}
    maker = get_sessionmaker()
    async with maker() as s:
        # 1. contacts document ------------------------------------------------------------------------------------------
        doc = (await s.execute(select(Document).where(Document.title == DOC_TITLE, Document.status != "superseded"))).scalars().first()
        records = contact_records()
        report["contacts_document"] = {"records": len(records), "existing_document_id": doc.id if doc else None}
        if apply and doc is None:
            st = LocalStorage()
            doc = Document(title=DOC_TITLE, file_path=st.save("offices_addresses_contacts.json",
                                                               json.dumps(records, ensure_ascii=False, indent=1).encode()), type="json")
            s.add(doc)
            await s.commit()
            report["contacts_document"]["status"] = await process_document(s, doc.id, storage=st)
            report["contacts_document"]["document_id"] = doc.id

        # needs-review items -> Unknown questions (not published)
        added = []
        for q, note in NEEDS_REVIEW:
            exists = (await s.execute(select(UnansweredQuestion).where(
                UnansweredQuestion.question_norm == normalize_question(q)))).scalars().first()
            if exists is None:
                added.append(q)
                if apply:
                    row = await learning.record_unknown(s, q, reason="needs_review")
                    row.draft_answer, row.status = note, "reviewing"
        report["needs_review"] = added

        # 2. variables -----------------------------------------------------------------------------------------------------
        created, kept = [], []
        for key, (value, desc, src) in VARIABLES.items():
            v = await s.get(Variable, key)
            if v is not None:
                kept.append(key)  # never overwrite a value an admin set
                continue
            created.append({"key": key, "value": value or "(empty: needs a value)", "source": src})
            if apply:
                s.add(Variable(key=key, value=value, description=desc))
        report["variables"] = {"created": created, "already_set": kept}

        # 3. Contact & Address Q&A -------------------------------------------------------------------------------------------
        existing = set((await s.execute(select(QAPair.question_norm))).scalars())
        todo = [(q, a) for q, a in CONTACT_QA if normalize_question(q) not in existing]
        report["contact_qa"] = {"added": [q for q, _ in todo], "already_there": len(CONTACT_QA) - len(todo)}
        if apply and todo:
            vecs = await embed_texts(s, [q for q, _ in todo])
            sig = await embedding_signature(s)
            for (q, a), vec in zip(todo, vecs):
                s.add(QAPair(question=q, question_norm=normalize_question(q), answer_template=a, category="Contact & Address",
                             active=True, embedding=vec, embedding_version=sig))
        if apply:
            await bump_context_version(s)
            await s.commit()

        # 4. keywords ---------------------------------------------------------------------------------------------------------
        if apply:
            report["keyword_rebuild"] = await rebuild_index(s)
            report["unknown_suggestions"] = await learning.refresh_suggestions(s)
            await s.commit()
            report["keyword_index"] = await stats(s)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    print(json.dumps(asyncio.run(run(ap.parse_args().apply)), ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
