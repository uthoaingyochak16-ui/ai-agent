"""Catalog of guest details kept on a profile, grouped for the dashboard.

The core fields (name, phone, email, location, age, profession, language) are columns on `profiles`; everything else
is a `profile_facts` row keyed by the catalog key. Staff and the AI extractor can also add keys outside the catalog
(any snake_case key); they show up under "Other".

`personal=True` fields identify the person, so - like name/phone - they are only saved after consent
(held encrypted until then).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

GROUPS = (
    ("basic", "Basic info"),
    ("contact", "Contact and address"),
    ("work", "Work and education"),
    ("family", "Family"),
    ("interests", "Interests and needs"),
    ("relationship", "With us"),
    ("other", "Other"),
)


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    group: str
    personal: bool = False
    hint: str = ""


CATALOG: tuple[FieldDef, ...] = (
    FieldDef("gender", "Gender", "basic", True),
    FieldDef("date_of_birth", "Date of birth", "basic", True),
    FieldDef("religion", "Religion", "basic", True),
    FieldDef("blood_group", "Blood group", "basic", True),
    FieldDef("address", "Address", "contact", True),
    FieldDef("city", "City / area", "contact", True),
    FieldDef("district", "District", "contact", True),
    FieldDef("country", "Country", "contact"),
    FieldDef("preferred_contact_time", "Best time to contact", "contact"),
    FieldDef("organization", "Organization / company", "work", True),
    FieldDef("designation", "Designation", "work"),
    FieldDef("education", "Education", "work"),
    FieldDef("institution", "School / university", "work", True),
    FieldDef("class_level", "Class / year", "work"),
    FieldDef("school", "School / college", "work", True),
    FieldDef("university", "University", "work", True),
    FieldDef("marital_status", "Marital status", "family", True),
    FieldDef("family_members", "Family members", "family", True),
    FieldDef("children", "Children", "family", True),
    FieldDef("interests", "Interests", "interests"),
    FieldDef("goals", "Goals", "interests"),
    FieldDef("needs", "What they are looking for", "interests"),
    FieldDef("concerns", "Concerns / problems mentioned", "interests"),
    FieldDef("health_conditions", "Health problems", "interests", True),
    FieldDef("courses_interested", "Courses interested in", "relationship"),
    FieldDef("courses_taken", "Courses taken", "relationship"),
    FieldDef("member_id", "Member ID", "relationship", True),
    FieldDef("referral_source", "How they heard of us", "relationship"),
    FieldDef("notes", "Staff notes", "other", hint="Only staff write this; the AI never overwrites it"),
)
BY_KEY = {f.key: f for f in CATALOG}
PERSONAL_FACT_KEYS = frozenset(f.key for f in CATALOG if f.personal)
STAFF_ONLY_KEYS = frozenset({"notes"})


def describe(key: str) -> dict:
    f = BY_KEY.get(key)
    if f:
        return {"label": f.label, "group": f.group}
    return {"label": key.replace("_", " ").capitalize(), "group": "other"}


def catalog_json() -> dict:
    return {"groups": [{"id": g, "label": label} for g, label in GROUPS], "fields": [asdict(f) for f in CATALOG]}


def extraction_keys_hint() -> str:
    return ", ".join(f.key for f in CATALOG if f.key not in STAFF_ONLY_KEYS)
