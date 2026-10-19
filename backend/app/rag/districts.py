"""The 64 districts of Bangladesh, for the program follow-up ("আপনি কোন জেলায় থাকেন?").

Matching is exact by district: a guest's district and a program's place match only when both name the same district
(any script / spelling below). There is deliberately no "nearby" table: a neighbouring district is never guessed.
"""
from __future__ import annotations

import re

# (Bangla, English, other Latin spellings / Banglish)
_DISTRICTS: list[tuple[str, str, tuple[str, ...]]] = [
    ("ঢাকা", "Dhaka", ("dhaka", "daka")), ("ফরিদপুর", "Faridpur", ("faridpur", "foridpur")),
    ("গাজীপুর", "Gazipur", ("gazipur", "gajipur")), ("গোপালগঞ্জ", "Gopalganj", ("gopalganj", "gopalgonj")),
    ("কিশোরগঞ্জ", "Kishoreganj", ("kishoreganj", "kishorganj", "kishorgonj")), ("মাদারীপুর", "Madaripur", ("madaripur",)),
    ("মানিকগঞ্জ", "Manikganj", ("manikganj", "manikgonj")), ("মুন্সীগঞ্জ", "Munshiganj", ("munshiganj", "munshigonj")),
    ("নারায়ণগঞ্জ", "Narayanganj", ("narayanganj", "narayangonj", "narayangong")), ("নরসিংদী", "Narsingdi", ("narsingdi", "narsindi")),
    ("রাজবাড়ী", "Rajbari", ("rajbari",)), ("শরীয়তপুর", "Shariatpur", ("shariatpur", "sariatpur")),
    ("টাঙ্গাইল", "Tangail", ("tangail",)),
    ("বাগেরহাট", "Bagerhat", ("bagerhat",)), ("চুয়াডাঙ্গা", "Chuadanga", ("chuadanga",)),
    ("যশোর", "Jashore", ("jashore", "jessore", "josor", "jeshore")), ("ঝিনাইদহ", "Jhenaidah", ("jhenaidah", "jhinaidah", "jhenaidaha")),
    ("খুলনা", "Khulna", ("khulna",)), ("কুষ্টিয়া", "Kushtia", ("kushtia", "kustia")), ("মাগুরা", "Magura", ("magura",)),
    ("মেহেরপুর", "Meherpur", ("meherpur",)), ("নড়াইল", "Narail", ("narail",)), ("সাতক্ষীরা", "Satkhira", ("satkhira", "shatkhira")),
    ("বগুড়া", "Bogura", ("bogura", "bogra")), ("জয়পুরহাট", "Joypurhat", ("joypurhat", "jaipurhat")),
    ("নওগাঁ", "Naogaon", ("naogaon", "noagaon", "nowgaon")), ("নাটোর", "Natore", ("natore", "nator")),
    ("চাঁপাইনবাবগঞ্জ", "Chapainawabganj", ("chapainawabganj", "chapai nawabganj", "chapai")), ("পাবনা", "Pabna", ("pabna",)),
    ("রাজশাহী", "Rajshahi", ("rajshahi",)), ("সিরাজগঞ্জ", "Sirajganj", ("sirajganj", "sirajgonj")),
    ("দিনাজপুর", "Dinajpur", ("dinajpur",)), ("গাইবান্ধা", "Gaibandha", ("gaibandha",)), ("কুড়িগ্রাম", "Kurigram", ("kurigram",)),
    ("লালমনিরহাট", "Lalmonirhat", ("lalmonirhat",)), ("নীলফামারী", "Nilphamari", ("nilphamari",)),
    ("পঞ্চগড়", "Panchagarh", ("panchagarh", "panchagar", "ponchogor")), ("রংপুর", "Rangpur", ("rangpur", "rongpur")),
    ("ঠাকুরগাঁও", "Thakurgaon", ("thakurgaon", "thakurgao")),
    ("হবিগঞ্জ", "Habiganj", ("habiganj", "hobiganj", "hobigonj")), ("মৌলভীবাজার", "Moulvibazar", ("moulvibazar", "moulvi bazar", "maulvibazar")),
    ("সুনামগঞ্জ", "Sunamganj", ("sunamganj", "sunamgonj")), ("সিলেট", "Sylhet", ("sylhet", "silet", "sileth")),
    ("বরগুনা", "Barguna", ("barguna", "borguna")), ("বরিশাল", "Barishal", ("barishal", "barisal", "borishal")),
    ("ভোলা", "Bhola", ("bhola",)), ("ঝালকাঠি", "Jhalokati", ("jhalokati", "jhalakathi", "jhalokathi")),
    ("পটুয়াখালী", "Patuakhali", ("patuakhali",)), ("পিরোজপুর", "Pirojpur", ("pirojpur",)),
    ("বান্দরবান", "Bandarban", ("bandarban",)), ("ব্রাহ্মণবাড়িয়া", "Brahmanbaria", ("brahmanbaria", "b baria", "bbaria")),
    ("চাঁদপুর", "Chandpur", ("chandpur",)), ("চট্টগ্রাম", "Chattogram", ("chattogram", "chittagong", "ctg", "chottogram")),
    ("কুমিল্লা", "Cumilla", ("cumilla", "comilla", "kumilla")), ("কক্সবাজার", "Cox's Bazar", ("cox's bazar", "coxs bazar", "cox bazar", "coxbazar")),
    ("ফেনী", "Feni", ("feni",)), ("খাগড়াছড়ি", "Khagrachhari", ("khagrachhari", "khagrachari")),
    ("লক্ষ্মীপুর", "Lakshmipur", ("lakshmipur", "laxmipur", "lokkhipur")), ("নোয়াখালী", "Noakhali", ("noakhali",)),
    ("রাঙ্গামাটি", "Rangamati", ("rangamati", "rangamatti")),
    ("জামালপুর", "Jamalpur", ("jamalpur",)), ("ময়মনসিংহ", "Mymensingh", ("mymensingh", "moymonsingh", "maymansingh")),
    ("নেত্রকোনা", "Netrokona", ("netrokona", "netrakona")), ("শেরপুর", "Sherpur", ("sherpur",)),
]
assert len(_DISTRICTS) == 64

# Bangla spelling variants seen in data ("রাঙামাটি", "বগুরা" ...)
_BN_VARIANTS = {"রাঙামাটি": "রাঙ্গামাটি", "বগুরা": "বগুড়া", "চাপাইনবাবগঞ্জ": "চাঁপাইনবাবগঞ্জ", "কক্স বাজার": "কক্সবাজার",
                "মুন্সিগঞ্জ": "মুন্সীগঞ্জ", "নারায়নগঞ্জ": "নারায়ণগঞ্জ", "গাজিপুর": "গাজীপুর", "মৌলভী বাজার": "মৌলভীবাজার",
                "ব্রাক্ষণবাড়িয়া": "ব্রাহ্মণবাড়িয়া", "লক্ষীপুর": "লক্ষ্মীপুর"}

_BY_BN = {bn: (bn, en) for bn, en, _ in _DISTRICTS}
_LATIN: list[tuple[re.Pattern, str]] = []
for _bn, _en, _aliases in _DISTRICTS:
    for _a in {_en.lower(), *_aliases}:
        # "dhaka", "dhakay", "dhaka te", "dhakar" ...
        _LATIN.append((re.compile(r"(?<![a-z])" + re.escape(_a) + r"(?:y|e|r|te|er|ey)?(?![a-z])"), _bn))


def find_districts(text: str) -> list[str]:
    """Districts named in the text (Bangla names, in order of appearance, no duplicates)."""
    t = text or ""
    for v, bn in _BN_VARIANTS.items():
        t = t.replace(v, bn)
    low = t.lower()
    found: list[tuple[int, str]] = []
    for bn in _BY_BN:
        i = t.find(bn)  # Bangla: substring, so ঢাকায় / ঢাকাতে / ঢাকার count
        if i >= 0:
            found.append((i, bn))
    for rx, bn in _LATIN:
        m = rx.search(low)
        if m:
            found.append((m.start(), bn))
    out: list[str] = []
    for _, bn in sorted(found):
        if bn not in out:
            out.append(bn)
    return out


def district_name(bn: str, lang: str) -> str:
    return _BY_BN[bn][1] if lang == "en" else bn
