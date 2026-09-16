from pathlib import Path


def ensure_verified_fixture(path: Path, email: str) -> None:
    text = path.read_text()
    old = f'        email="{email}",\n'
    new = old + "        email_verified=True,\n"
    if new not in text:
        if old not in text:
            raise SystemExit(f"fixture anchor missing: {email}")
        text = text.replace(old, new, 1)
    path.write_text(text)


conftest = Path("tests/conftest.py")
for fixture_email in (
    "test@example.com",
    "donor@example.com",
    "third@example.com",
    "recent@example.com",
):
    ensure_verified_fixture(conftest, fixture_email)

chat_test = Path("tests/test_api_chat.py")
text = chat_test.read_text()
old = '        email="nolocation@example.com",\n'
new = old + "        email_verified=True,\n"
if new not in text:
    if old not in text:
        raise SystemExit("nomad fixture anchor missing")
    text = text.replace(old, new, 1)

old_assertion = '''    # The real donor reached the summarisation prompt, so the model is
    # summarising data rather than inventing it.
    summary_prompt = str(calls[1])
    assert donor_user.name in summary_prompt
'''
new_assertion = '''    # The summarisation prompt contains the real donor's public display data,
    # but privacy-sensitive contact details are not sent to Gemini.
    summary_prompt = "\\n".join(
        part.text or ""
        for content in calls[1]["contents"]
        for part in content.parts
    )
    assert donor_user.name in summary_prompt
    assert donor_user.phone not in summary_prompt
'''
if new_assertion not in text:
    if old_assertion not in text:
        raise SystemExit("Gemini summary assertion anchor missing")
    text = text.replace(old_assertion, new_assertion, 1)
chat_test.write_text(text)

# This helper is bootstrap-only and must not survive in the clean repository.
Path(__file__).unlink()
