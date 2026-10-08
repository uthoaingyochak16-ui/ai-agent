from app.privacy.redaction import ALL_KINDS, SENSITIVE_KINDS, Redactor, redact_for_storage


def test_redacts_all_kinds_and_restores():
    r = Redactor(known_names=("Rahim Uddin",))
    text = ("I'm Rahim Uddin, call 01712345678, mail Rahim@Example.com, card 4111 1111 1111 1111, "
            "NID: 1234567890123, House 12, Road 5, Dhanmondi")
    out = r.redact(text)
    for raw in ("Rahim", "01712345678", "example.com", "4111", "1234567890123", "Dhanmondi"):
        assert raw.lower() not in out.lower(), raw
    for ph in ("[NAME_1]", "[PHONE_1]", "[EMAIL_1]", "[CARD_1]", "[NID_1]", "[ADDRESS_1]"):
        assert ph in out
    assert r.restore("Hi [NAME_1], we noted [PHONE_1]") == "Hi Rahim, we noted 01712345678"


def test_bengali_digits_and_same_value_same_placeholder():
    r = Redactor()
    out = r.redact("০১৭১২৩৪৫৬৭৮ আবার ০১৭১২৩৪৫৬৭৮")
    assert out.count("[PHONE_1]") == 2 and "[PHONE_2]" not in out


def test_non_luhn_long_number_is_not_a_card():
    out = Redactor().redact("order 1234567890123456 ok")
    assert "[CARD_" not in out


def test_unknown_placeholders_from_the_model_are_stripped():
    assert Redactor().restore("see [PHONE_7] and [EMAIL_3] now") == "see  and  now"


def test_name_modes():
    r = Redactor(known_names=("Rahim Uddin",), name_mode="first_name")
    out = r.redact("Rahim Uddin said Rahim is here")
    assert "Uddin" not in out and "Rahim is here" in out
    assert Redactor(name_mode="none").redact("Rahim") == "Rahim"
    r2 = Redactor(known_names=("Rahim Uddin",))
    assert r2.name_for_prompt("Rahim Uddin") == "[NAME_1]"


def test_permissive_mode_still_redacts_sensitive_kinds():
    r = Redactor(kinds=SENSITIVE_KINDS)
    out = r.redact("call 01712345678 card 4111111111111111")
    assert "01712345678" in out and "[CARD_1]" in out


def test_custom_patterns():
    r = Redactor(custom_patterns=(r"ORD-\d{6}",))
    assert r.redact("my order ORD-123456") == "my order [CUSTOM_1]"


def test_findings_never_contain_original_values():
    r = Redactor()
    r.redact("mail a@b.com phone 01712345678")
    assert "a@b.com" not in str(r.summary()) and "0171" not in str(r.summary())


def test_storage_redaction_keeps_names_readable():
    out = redact_for_storage("I am Rahim, phone 01712345678")
    assert "Rahim" in out and "01712345678" not in out
    assert ALL_KINDS >= SENSITIVE_KINDS
