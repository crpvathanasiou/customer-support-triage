import asyncio

import pytest
from pydantic import BaseModel, ConfigDict
from typing import Protocol
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionSystemMessageParam, ChatCompletionUserMessageParam
from app.core.exceptions import (
    GuardrailBlockedError,
    ModelOutputParsingError,
    UpstreamServiceError,
)
from app.llm.openai_wrapper import (
    AsyncOpenAIWrapper,
    BaseGuardrail,
    GuardrailResult,
    MaxPromptLengthGuardrail,
)

"""
Καλύπτει 7 βασικά cases:

generate_text επιστρέφει plain text σωστά
generate_structured επιστρέφει parsed Pydantic object
input guardrail μπλοκάρει request
max prompt length guardrail δουλεύει
structured parsing αποτυγχάνει όταν parsed is None
text upstream failure γίνεται UpstreamServiceError
structured unexpected failure γίνεται ModelOutputParsingError
"""
class DummyStructuredResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    risk_level: str


class RejectAllGuardrail(BaseGuardrail):
    name = "reject_all"

    def check_input(self, *, prompt: str, model_name: str, temperature: float) -> GuardrailResult:
        return GuardrailResult(
            passed=False,
            reason="Rejected by test guardrail.",
        )


class FakeTextMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeTextChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeTextMessage(content)


class FakeTextCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [FakeTextChoice(content)]


class FakeStructuredMessage:
    def __init__(self, parsed=None, content="") -> None:
        self.parsed = parsed
        self.content = content


class FakeStructuredChoice:
    def __init__(self, parsed=None, content="") -> None:
        self.message = FakeStructuredMessage(parsed=parsed, content=content)


class FakeStructuredCompletion:
    def __init__(self, parsed=None, content="") -> None:
        self.choices = [FakeStructuredChoice(parsed=parsed, content=content)]


class FakeChatCompletions:
    def __init__(self, text_response: str = "ok") -> None:
        self._text_response = text_response

    async def create(self, **kwargs):
        return FakeTextCompletion(self._text_response)


class FakeBetaChatCompletions:
    def __init__(self, parsed_response=None, content: str = "") -> None:
        self._parsed_response = parsed_response
        self._content = content

    async def parse(self, **kwargs):
        return FakeStructuredCompletion(
            parsed=self._parsed_response,
            content=self._content,
        )


class FakeChat:
    def __init__(self, text_response: str = "ok") -> None:
        self.completions = FakeChatCompletions(text_response=text_response)


class FakeBetaChat:
    def __init__(self, parsed_response=None, content: str = "") -> None:
        self.completions = FakeBetaChatCompletions(
            parsed_response=parsed_response,
            content=content,
        )


class FakeBeta:
    def __init__(self, parsed_response=None, content: str = "") -> None:
        self.chat = FakeBetaChat(
            parsed_response=parsed_response,
            content=content,
        )


class FakeAsyncOpenAIClient:
    def __init__(
        self,
        *,
        text_response: str = "plain text response",
        parsed_response=None,
        structured_content: str = '{"decision":"allow","risk_level":"low"}',
    ) -> None:
        self.chat = FakeChat(text_response=text_response)
        self.beta = FakeBeta(
            parsed_response=parsed_response,
            content=structured_content,
        )


class FakeFailingTextChatCompletions:
    async def create(self, **kwargs):
        raise RuntimeError("text call failed")


class FakeFailingStructuredChatCompletions:
    async def parse(self, **kwargs):
        raise RuntimeError("structured parse failed")


class FakeFailingChat:
    def __init__(self) -> None:
        self.completions = FakeFailingTextChatCompletions()


class FakeFailingBetaChat:
    def __init__(self) -> None:
        self.completions = FakeFailingStructuredChatCompletions()


class FakeFailingBeta:
    def __init__(self) -> None:
        self.chat = FakeFailingBetaChat()


class FakeFailingAsyncOpenAIClient:
    def __init__(self) -> None:
        self.chat = FakeFailingChat()
        self.beta = FakeFailingBeta()


@pytest.mark.asyncio
async def test_generate_text_returns_plain_text_result():
    client = FakeAsyncOpenAIClient(text_response="hello from model")

    wrapper = AsyncOpenAIWrapper(
        client=client,
        default_model="gpt-test",
        default_temperature=0.0,
    )

    result = await wrapper.generate_text(
        prompt="Say hello",
    )

    assert result.model_name == "gpt-test"
    assert result.raw_text == "hello from model"
    assert result.parsed is None
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_generate_structured_returns_parsed_pydantic_object():
    parsed = DummyStructuredResponse(
        decision="allow",
        risk_level="low",
    )
    client = FakeAsyncOpenAIClient(
        parsed_response=parsed,
        structured_content='{"decision":"allow","risk_level":"low"}',
    )

    wrapper = AsyncOpenAIWrapper(
        client=client,
        default_model="gpt-test",
        default_temperature=0.0,
    )

    result = await wrapper.generate_structured(
        prompt="Classify this message",
        response_schema=DummyStructuredResponse,
    )

    assert result.model_name == "gpt-test"
    assert result.parsed is not None
    assert isinstance(result.parsed, DummyStructuredResponse)
    assert result.parsed.decision == "allow"
    assert result.parsed.risk_level == "low"
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_generate_text_blocks_when_input_guardrail_fails():
    client = FakeAsyncOpenAIClient()

    wrapper = AsyncOpenAIWrapper(
        client=client,
        default_model="gpt-test",
        default_temperature=0.0,
    )

    with pytest.raises(GuardrailBlockedError):
        await wrapper.generate_text(
            prompt="This should be blocked",
            enforced_guardrails=[RejectAllGuardrail()],
        )


@pytest.mark.asyncio
async def test_generate_structured_blocks_when_prompt_too_long():
    client = FakeAsyncOpenAIClient(
        parsed_response=DummyStructuredResponse(decision="allow", risk_level="low")
    )

    wrapper = AsyncOpenAIWrapper(
        client=client,
        default_model="gpt-test",
        default_temperature=0.0,
    )

    with pytest.raises(GuardrailBlockedError):
        await wrapper.generate_structured(
            prompt="x" * 100,
            response_schema=DummyStructuredResponse,
            enforced_guardrails=[MaxPromptLengthGuardrail(max_chars=10)],
        )


@pytest.mark.asyncio
async def test_generate_structured_raises_parsing_error_when_parsed_is_none():
    client = FakeAsyncOpenAIClient(
        parsed_response=None,
        structured_content="",
    )

    wrapper = AsyncOpenAIWrapper(
        client=client,
        default_model="gpt-test",
        default_temperature=0.0,
    )

    with pytest.raises(ModelOutputParsingError):
        await wrapper.generate_structured(
            prompt="Return structured output",
            response_schema=DummyStructuredResponse,
        )


@pytest.mark.asyncio
async def test_generate_text_raises_upstream_error_on_transport_failure():
    client = FakeFailingAsyncOpenAIClient()

    wrapper = AsyncOpenAIWrapper(
        client=client,
        default_model="gpt-test",
        default_temperature=0.0,
    )
    wrapper.max_retries = 0

    with pytest.raises(UpstreamServiceError):
        await wrapper.generate_text(
            prompt="Hello",
        )


@pytest.mark.asyncio
async def test_generate_structured_raises_parsing_error_on_unexpected_failure():
    client = FakeFailingAsyncOpenAIClient()

    wrapper = AsyncOpenAIWrapper(
        client=client,
        default_model="gpt-test",
        default_temperature=0.0,
    )
    wrapper.max_retries = 0

    with pytest.raises(ModelOutputParsingError):
        await wrapper.generate_structured(
            prompt="Classify",
            response_schema=DummyStructuredResponse,
        )