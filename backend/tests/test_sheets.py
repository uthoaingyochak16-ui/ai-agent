"""Data sheets: spreadsheet-like tables (e.g. Books) that the AI searches; the Knowledge page's search and overview."""
import io

from sqlalchemy import func, select

from app.models import DataRow, DataSheet, KeywordEntry
from app.services.tools import ToolContext, _search_sheet
from tests.test_admin_api import login

BOOKS = [{"label": "Name"}, {"label": "Edition"}, {"label": "Author"}, {"label": "Publication"}, {"label": "Price"},
         {"label": "Description"}]


async def make_books(client, admin):
    r = await client.post("/api/sheets", json={"name": "Books", "description": "Books published by Quantum Foundation",
                                                "columns": BOOKS}, headers=admin)
    assert r.status_code == 200, r.text
    sheet = r.json()
    keys = {c["label"]: c["key"] for c in sheet["columns"]}
    for name, author, price in (("Sukhi Paribar", "Mahajatak", "250"), ("Atmo Unnayan", "Mahajatak", "300"),
                                ("Rannar Boi", "Rina Akter", "180")):
        r = await client.post(f"/api/sheets/{sheet['id']}/rows", headers=admin,
                              json={"values": {keys["Name"]: name, keys["Author"]: author, keys["Price"]: price, "nope": "x"}})
        assert r.status_code == 200 and "nope" not in r.json()["values"]
    return sheet, keys


async def test_create_edit_and_search_a_sheet(client, maker):
    admin = await login(client, maker)
    sheet, keys = await make_books(client, admin)
    assert [c["key"] for c in sheet["columns"]] == ["name", "edition", "author", "publication", "price", "description"]
    rows = (await client.get(f"/api/sheets/{sheet['id']}/rows", params={"q": "mahajatak"}, headers=admin)).json()
    assert rows["total"] == 2
    # rename a column: its values stay; remove one: its values go
    cols = [{"key": c["key"], "label": "Writer" if c["key"] == "author" else c["label"]} for c in sheet["columns"] if c["key"] != "price"]
    upd = (await client.put(f"/api/sheets/{sheet['id']}", json={"columns": cols}, headers=admin)).json()
    assert {c["label"] for c in upd["columns"]} >= {"Writer"} and upd["rows"] == 3
    one = (await client.get(f"/api/sheets/{sheet['id']}/rows", params={"q": "rannar"}, headers=admin)).json()["items"][0]
    assert one["values"] == {"name": "Rannar Boi", "author": "Rina Akter"}
    assert (await client.post("/api/sheets", json={"name": "books", "columns": BOOKS}, headers=admin)).status_code == 409
    agent = await login(client, maker, "agent", "agent@x.com")
    assert (await client.post("/api/sheets", json={"name": "X", "columns": BOOKS}, headers=agent)).status_code == 403


async def test_rows_are_in_the_keyword_index_and_reach_the_answer_context(client, maker, session):
    admin = await login(client, maker)
    sheet, _ = await make_books(client, admin)
    async with maker() as s:
        n = (await s.execute(select(func.count()).select_from(KeywordEntry).where(KeywordEntry.source_type == "sheetrow"))).scalar_one()
    assert n > 0
    from app.rag.keyword_search import keyword_retrieve
    async with maker() as s:
        res = await keyword_retrieve(s, None, text="Sukhi Paribar boi er dam koto?", retrieval_text="Sukhi Paribar boi er dam koto?",
                                     history=[], channel=None, conversation_id=None, allow_ai=False)
    assert any(e.startswith("SHEET Books — Name: Sukhi Paribar") and "Price: 250" in e for e in res.extra), res.extra
    # switched off: the AI no longer sees it; deleted: its index rows go too
    await client.put(f"/api/sheets/{sheet['id']}", json={"active": False}, headers=admin)
    async with maker() as s:
        res = await keyword_retrieve(s, None, text="Sukhi Paribar", retrieval_text="Sukhi Paribar", history=[], channel=None,
                                     conversation_id=None, allow_ai=False)
    assert not any(e.startswith("SHEET") for e in res.extra)
    await client.delete(f"/api/sheets/{sheet['id']}", headers=admin)
    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(DataRow))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(KeywordEntry).where(KeywordEntry.source_type == "sheetrow"))).scalar_one() == 0


async def test_search_sheet_tool_finds_or_lists_rows(client, maker, session):
    admin = await login(client, maker)
    await make_books(client, admin)
    async with maker() as s:
        ctx = ToolContext(s, None, None, "")
        found = await _search_sheet(ctx, {"sheet": "books", "query": "Mahajatak"})
        listed = await _search_sheet(ctx, {"sheet": "Books", "query": ""})
        missing = await _search_sheet(ctx, {"sheet": "Cars", "query": "x"})
    assert found.count("SHEET Books") == 2 and "Rannar" not in found
    assert listed.count("SHEET Books") == 3
    assert "no sheet called 'Cars'" in missing and "Books" in missing


async def test_import_a_spreadsheet_as_a_new_sheet_then_add_more_and_export(client, maker):
    admin = await login(client, maker)
    csv1 = "Branch,District,Phone\nMirpur,Dhaka,01700000001\nAgrabad,Chattogram,01700000002\n"
    r = (await client.post("/api/sheets/import", files={"file": ("Branches.csv", io.BytesIO(csv1.encode()), "text/csv")},
                           data={"description": "Our branches"}, headers=admin)).json()
    assert (r["sheet"]["name"], r["added"], r["sheet"]["description"]) == ("Branches", 2, "Our branches")
    csv2 = "branch,Email\nSylhet,s@x.org\n"
    r2 = (await client.post("/api/sheets/import", files={"file": ("more.csv", io.BytesIO(csv2.encode()), "text/csv")},
                            data={"sheet_id": str(r["sheet"]["id"])}, headers=admin)).json()
    assert r2["added"] == 1 and [c["label"] for c in r2["sheet"]["columns"]] == ["Branch", "District", "Phone", "Email"]
    out = (await client.get(f"/api/sheets/{r['sheet']['id']}/export", headers=admin)).text
    assert out.lstrip("﻿").splitlines()[0] == "Branch,District,Phone,Email" and "Sylhet,,,s@x.org" in out


async def test_knowledge_search_finds_every_kind_and_overview_counts(client, maker, session):
    admin = await login(client, maker)
    await make_books(client, admin)
    await client.post("/api/qa", json={"question": "Sukhi Paribar boi kothay pabo?", "answer_template": "Quantum er sob shakhay."},
                      headers=admin)
    r = (await client.get("/api/knowledge/search", params={"q": "sukhi paribar"}, headers=admin)).json()
    assert [x["sheet"] for x in r["sheets"]] == ["Books"] and r["qa"][0]["question"].startswith("Sukhi Paribar")
    o = (await client.get("/api/knowledge/overview", headers=admin)).json()
    assert o["qa"] == 1 and [s["name"] for s in o["sheets"]] == ["Books"] and o["sheets"][0]["rows"] == 3
    async with maker() as s:
        assert (await s.execute(select(DataSheet.name))).scalar_one() == "Books"


async def test_the_sheet_tool_is_offered_only_when_a_sheet_has_rows(client, maker):
    from app.services.tools import enabled_tools
    async with maker() as s:
        assert "search_sheet" not in [t.spec.name for t in await enabled_tools(s)]
    admin = await login(client, maker)
    await make_books(client, admin)
    async with maker() as s:
        assert "search_sheet" in [t.spec.name for t in await enabled_tools(s)]
