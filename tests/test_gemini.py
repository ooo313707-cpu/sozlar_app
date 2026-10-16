import json

import pytest

from app import database as db
from app import gemini_service as gs


class FakeErr(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class FakeModels:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "system": config.system_instruction})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return type("R", (), {"text": step})()


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


@pytest.fixture
def fake(monkeypatch, fresh_db):
    sleeps = []
    monkeypatch.setattr(gs, "_sleep", lambda s: sleeps.append(s))

    def install(script):
        client = FakeClient(script)
        monkeypatch.setattr(gs, "get_client", lambda: client)
        return client, sleeps
    return install


def test_is_retryable():
    assert gs._is_retryable(FakeErr("x", code=429))
    assert gs._is_retryable(FakeErr("x", code=503))
    assert gs._is_retryable(RuntimeError("503 UNAVAILABLE: model overloaded"))
    assert gs._is_retryable(RuntimeError("RESOURCE_EXHAUSTED quota"))
    assert not gs._is_retryable(RuntimeError("API key not valid"))
    assert not gs._is_retryable(FakeErr("bad request", code=400))


def test_retry_then_success(fake):
    client, sleeps = fake([FakeErr("busy", 503), FakeErr("limit", 429), '{"ok": 1}'])
    assert gs._generate_json("sys", "hi") == {"ok": 1}
    assert len(client.models.calls) == 3 and sleeps == [2, 5]


def test_non_retryable_raises_immediately(fake):
    client, sleeps = fake([RuntimeError("API key not valid")])
    with pytest.raises(RuntimeError):
        gs._generate_json("sys", "hi")
    assert len(client.models.calls) == 1 and sleeps == []


def test_retry_exhausted(fake):
    client, sleeps = fake([FakeErr("busy", 503)] * 4)
    with pytest.raises(FakeErr):
        gs._generate_json("sys", "hi")
    assert len(client.models.calls) == 4 and sleeps == [2, 5, 10]


def test_no_key_raises(fresh_db):
    with pytest.raises(RuntimeError):
        gs._generate_json("s", "c")


def test_generate_questions_prompt_and_parse(fake):
    client, _ = fake([json.dumps({"passage": "", "questions": [{"word": "a"}]})])
    items = [{"word": "meticulous", "translation": "sinchkov", "type": "cloze", "leech": True},
             {"word": "reluctant", "translation": "istamaydigan", "type": "meaning", "leech": False}]
    out = gs.generate_questions(items, level="C1", mode="B")
    assert out["questions"] == [{"word": "a"}]
    call = client.models.calls[0]
    assert "C1" in call["system"] and "umumiy takrorlash" in call["system"]
    assert "meticulous — sinchkov | type=cloze | leech" in call["contents"]
    assert "reluctant — istamaydigan | type=meaning" in call["contents"]
    assert "reluctant — istamaydigan | type=meaning | leech" not in call["contents"]


def test_grade_batch_parse_and_ignores_bad_ids(fake):
    payload = {"results": [{"id": 1, "word_ok": True, "grammar_ok": True, "natural": True, "feedback": "a"},
                           {"id": "2", "word_ok": False, "grammar_ok": True},
                           {"id": "x"}, {"nope": 1}, "axlat"]}
    client, _ = fake([json.dumps(payload)])
    out = gs.grade_batch([{"id": 1, "word": "w", "answer": "..."}], level="A2")
    assert set(out) == {1, 2} and out[2]["word_ok"] is False
    assert "A2" in client.models.calls[0]["system"]
    assert json.loads(client.models.calls[0]["contents"])[0]["id"] == 1


def test_grade_batch_bad_shape(fake):
    fake(['{"results": "bad"}'])
    assert gs.grade_batch([]) == {}
