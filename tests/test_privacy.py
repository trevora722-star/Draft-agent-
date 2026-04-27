from npo_agent.privacy import has_pii, scrub


def test_scrub_replaces_email():
    result = scrub("Contact jane.doe@example.com for details.")
    assert "jane.doe@example.com" not in result.text
    assert "[EMAIL_1]" in result.text
    assert result.mapping["[EMAIL_1]"] == "jane.doe@example.com"


def test_scrub_replaces_canadian_phone():
    result = scrub("Call (604) 555-1234 or 778-555-9999.")
    assert "604" not in result.text
    assert "[PHONE_1]" in result.text
    assert "[PHONE_2]" in result.text


def test_scrub_replaces_sin():
    result = scrub("SIN: 123-456-789")
    assert "123-456-789" not in result.text
    assert "[SIN_1]" in result.text


def test_scrub_replaces_postal_code():
    result = scrub("Mail to V6B 1A1, Vancouver.")
    assert "V6B 1A1" not in result.text
    assert "[POSTAL_1]" in result.text


def test_scrub_replaces_phn():
    # BC PHNs start with 9
    result = scrub("PHN 9123456789 needs follow-up.")
    assert "9123456789" not in result.text
    assert "[PHN_1]" in result.text


def test_scrub_replaces_dob():
    result = scrub("DOB: 03/15/1985")
    assert "1985" not in result.text
    assert "[DOB_1]" in result.text


def test_scrub_preserves_allowlisted_geographic_names():
    text = "We serve clients across British Columbia and the Lower Mainland."
    result = scrub(text)
    # Geographic / institutional names should NOT be scrubbed.
    assert "British Columbia" in result.text
    assert "Lower Mainland" in result.text


def test_scrub_replaces_person_names():
    result = scrub("Sarah Mitchell met with Dr. Henry Patel.")
    assert "Sarah Mitchell" not in result.text
    assert "Henry Patel" not in result.text
    # Two distinct people → two distinct placeholders.
    assert "[NAME_1]" in result.text
    assert "[NAME_2]" in result.text


def test_rehydrate_restores_originals():
    result = scrub("Email jane@example.com about her enrollment.")
    model_output = "Send the receipt to [EMAIL_1] within 7 days."
    rehydrated = result.rehydrate(model_output)
    assert "jane@example.com" in rehydrated
    assert "[EMAIL_1]" not in rehydrated


def test_has_pii_detects_obvious_cases():
    assert has_pii("Reach me at user@example.com")
    assert has_pii("Phone: 604-555-0000")
    assert has_pii("SIN: 111-222-333")


def test_has_pii_negative():
    assert not has_pii("Our program serves families in the Fraser Valley.")
