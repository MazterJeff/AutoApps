import json
import unittest
from types import SimpleNamespace

from autoapps.llm import ANSWERS_SCHEMA, LLM, LLMError
from autoapps.models import FormField, Job

from .helpers import fake_message, sample_profile, temp_config


class FakeMessages:
    def __init__(self, responses, fail_with=None):
        self.responses = list(responses)
        self.fail_with = fail_with
        self.calls = []

    def create(self, **params):
        self.calls.append(params)
        if self.fail_with and "fallbacks" in params:
            raise self.fail_with
        return self.responses.pop(0)


def fake_client(responses, fail_with=None):
    beta = FakeMessages(responses, fail_with)
    plain = FakeMessages(responses)
    plain.responses = beta.responses  # share the queue
    return SimpleNamespace(beta=SimpleNamespace(messages=beta), messages=plain), beta, plain


JOB = Job(id="greenhouse:acme:1", source="greenhouse", company="Acme", title="SWE Intern", description="Python")


class LLMTest(unittest.TestCase):
    def test_answer_questions_request_shape(self):
        payload = {"answers": [{"key": "q1", "value": "Because.", "values": [], "confidence": "medium", "note": ""}]}
        client, beta, _ = fake_client([fake_message(json.dumps(payload))])
        llm = LLM(temp_config(), client=client)
        fields = [FormField(key="q1", label="Why Acme?", type="textarea", required=True)]
        out = llm.answer_questions(sample_profile(), JOB, fields)
        self.assertEqual(out["q1"]["value"], "Because.")
        params = beta.calls[0]
        self.assertEqual(params["model"], "claude-opus-5-5")
        self.assertEqual(params["fallbacks"], "default")
        self.assertEqual(params["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(params["output_config"]["format"]["schema"], ANSWERS_SCHEMA)
        self.assertNotIn("thinking", params)
        self.assertNotIn("temperature", params)
        # profile + resume are cached in the system prompt; the job and questions go in the user turn
        self.assertEqual(params["system"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Built a compiler", params["system"][-1]["text"])
        self.assertIn("Why Acme?", params["messages"][0]["content"])

    def test_retries_without_fallbacks_when_rejected(self):
        client, beta, plain = fake_client([fake_message("Dear Hiring Team, ...")], fail_with=ValueError("fallbacks: not supported"))
        llm = LLM(temp_config(), client=client)
        self.assertTrue(llm.write_cover_letter(sample_profile(), JOB).startswith("Dear"))
        self.assertEqual(len(plain.calls), 1)
        self.assertNotIn("fallbacks", plain.calls[0])
        self.assertFalse(llm._fallbacks)

    def test_refusal_and_errors_become_llm_errors(self):
        client, _, _ = fake_client([fake_message("", stop_reason="refusal")])
        with self.assertRaises(LLMError):
            LLM(temp_config(), client=client).write_cover_letter(sample_profile(), JOB)
        client, _, _ = fake_client([], fail_with=RuntimeError("401 invalid x-api-key"))
        with self.assertRaises(LLMError):
            LLM(temp_config(), client=client).write_cover_letter(sample_profile(), JOB)

    def test_no_questions_no_call(self):
        client, beta, _ = fake_client([])
        self.assertEqual(LLM(temp_config(), client=client).answer_questions(sample_profile(), JOB, []), {})
        self.assertEqual(beta.calls, [])


if __name__ == "__main__":
    unittest.main()
