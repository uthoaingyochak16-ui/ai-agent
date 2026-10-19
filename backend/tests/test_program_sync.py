from datetime import date

from app.services.program_sync import parse_programs_page


def test_program_sync_reads_future_event_jsonld_only():
    page = '''<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
      {"@type":"Event","name":"Quantum Method Course","startDate":"2026-11-20","endDate":"2026-11-23","location":{"name":"Dhaka"}},
      {"@type":"Event","name":"Old event","startDate":"2026-01-01"}
    ]}</script><p>2026-12-10 is unrelated news</p>'''
    assert parse_programs_page(page, date(2026, 10, 19)) == [{"name": "Quantum Method Course", "start_date": date(2026, 11, 20), "end_date": date(2026, 11, 23), "date_text": "2026-11-20", "place": "Dhaka"}]


def test_program_sync_reads_official_event_api():
    page = '{"results":[{"title":"Upcoming program","start_date":"2026-11-20","end_date":"2026-11-23","venue":"Dhaka","is_published":true},{"title":"Hidden","start_date":"2026-11-20","is_published":false}]}'
    assert parse_programs_page(page, date(2026, 10, 19))[0]["name"] == "Upcoming program"
