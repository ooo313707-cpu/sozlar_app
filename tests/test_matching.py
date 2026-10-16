from app import gemini_service as gs


def W(i, word):
    return {"id": i, "word": word, "translation": "t"}


WORDS = [W(1, "meticulous"), W(2, "ice cream"), W(3, "look up")]


def test_exact_and_case():
    assert gs._match_word("Meticulous", WORDS)["id"] == 1
    assert gs._match_word("ice-cream", WORDS)["id"] == 2
    assert gs._match_word("look up.", WORDS)["id"] == 3


def test_partial_match():
    assert gs._match_word("meticulously", WORDS)["id"] == 1


def test_no_match():
    assert gs._match_word("banana", WORDS) is None
    assert gs._match_word("", WORDS) is None


def test_json_instruction_count():
    assert "AYNAN 14 ta" in gs._json_instruction(14)
    assert "3 tadan 4 tagacha" in gs._json_instruction(None)
