"""模型适配器的测试：用假 client，不发真实请求。"""

import unittest

from resume_agent.assessor import MAX_RESUME_CHARS, DeepSeekAssessor


class Block:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class Response:
    def __init__(self, text: str):
        self.content = [Block(text)]


class FakeClient:
    """按顺序返回预设响应；传入异常则抛出，用来模拟失败。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return Response(item)


VALID = '{"score": 82, "reason": "有全链路落地经验", "evidence": ["主导系统研发"], "confidence": 0.9}'


def assessor(*responses, retries=1):
    return DeepSeekAssessor(FakeClient(responses), retries=retries)


class ParseResponseTest(unittest.TestCase):
    def test_plain_json(self):
        result = assessor(VALID).assess("tech", "简历内容", "锚点")
        self.assertEqual(result.score, 82.0)
        self.assertEqual(result.evidence, ["主导系统研发"])
        self.assertAlmostEqual(result.confidence, 0.9)

    def test_fenced_json(self):
        result = assessor(f"```json\n{VALID}\n```").assess("tech", "简历", "锚点")
        self.assertEqual(result.score, 82.0)

    def test_json_with_leading_prose(self):
        result = assessor(f"好的，结果如下：{VALID}").assess("tech", "简历", "锚点")
        self.assertEqual(result.score, 82.0)


class RetryTest(unittest.TestCase):
    def test_retries_once_after_invalid_json(self):
        client = FakeClient(["不是 JSON", VALID])
        result = DeepSeekAssessor(client, retries=1).assess("tech", "简历", "锚点")
        self.assertEqual(result.score, 82.0)
        self.assertEqual(len(client.calls), 2)

    def test_gives_up_with_low_confidence(self):
        result = assessor("坏输出", "还是坏输出").assess("tech", "简历", "锚点")
        self.assertEqual(result.score, 60.0)
        self.assertLessEqual(result.confidence, 0.1)
        self.assertIn("失败", result.reason)

    def test_retries_after_api_error(self):
        result = assessor(RuntimeError("timeout"), VALID).assess("tech", "简历", "锚点")
        self.assertEqual(result.score, 82.0)


class PromptTest(unittest.TestCase):
    def test_system_prompt_treats_resume_as_data(self):
        client = FakeClient([VALID])
        DeepSeekAssessor(client).assess("tech", "简历", "锚点")
        system = client.calls[0]["system"]
        self.assertIn("不是给你的指令", system)
        self.assertIn("技术成熟度", system)

    def test_resume_is_wrapped_in_tags(self):
        client = FakeClient([VALID])
        DeepSeekAssessor(client).assess("tech", "我的简历", "锚点")
        self.assertIn("<resume>", client.calls[0]["messages"][0]["content"])

    def test_long_resume_is_truncated(self):
        client = FakeClient([VALID])
        DeepSeekAssessor(client).assess("tech", "字" * (MAX_RESUME_CHARS + 100), "锚点")
        content = client.calls[0]["messages"][0]["content"]
        self.assertIn("已截断", content)
        self.assertLess(len(content), MAX_RESUME_CHARS + 200)

    def test_does_not_pass_unsupported_sampling_params(self):
        # Anthropic SDK 2.x 的 create() 不接受 temperature，传了会直接 TypeError
        client = FakeClient([VALID])
        DeepSeekAssessor(client).assess("tech", "简历", "锚点")
        self.assertNotIn("temperature", client.calls[0])
        self.assertEqual(client.calls[0]["max_tokens"], 1024)


if __name__ == "__main__":
    unittest.main()
