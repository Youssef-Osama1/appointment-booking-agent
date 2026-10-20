import logging
import time

from openai import AsyncOpenAI, BadRequestError
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from pydantic import BaseModel

from app.config.settings import Settings
from app.core.exceptions import LLMOutputError

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        self.model = settings.llm_model
        self._temperature = settings.llm_temperature

    async def complete(self, prompt: str, system: str | None = None) -> str:
        started = time.perf_counter()
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(prompt, system),
            temperature=self._temperature,
        )
        choice = self._log_response(response, time.perf_counter() - started)
        return choice.message.content or ""

    async def extract[T: BaseModel](
        self, prompt: str, schema: type[T], system: str | None = None
    ) -> T:
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.parse(
                model=self.model,
                messages=self._build_messages(prompt, system),
                temperature=self._temperature,
                response_format=schema,
            )
        except BadRequestError as error:
            if error.code == "json_validate_failed":
                raise LLMOutputError(
                    f"Model output did not match {schema.__name__}"
                ) from error
            raise
        choice = self._log_response(response, time.perf_counter() - started)

        parsed = choice.message.parsed
        if parsed is None:
            raise LLMOutputError(
                f"Model returned no {schema.__name__}: refusal={choice.message.refusal!r}"
            )
        return parsed

    async def chat(
        self, messages: list[dict], tools: list[dict]
    ) -> ChatCompletionMessage:
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                temperature=self._temperature,
            )
        except BadRequestError as error:
            if error.code == "tool_use_failed":
                raise LLMOutputError("Model produced an invalid tool call") from error
            raise
        choice = self._log_response(response, time.perf_counter() - started)
        return choice.message

    async def close(self) -> None:
        await self._client.close()

    def _build_messages(self, prompt: str, system: str | None) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _log_response(self, response: ChatCompletion, elapsed: float) -> Choice:
        choice = response.choices[0]
        usage = response.usage
        logger.info(
            "model=%s latency=%.2fs input_tokens=%s output_tokens=%s finish_reason=%s",
            self.model,
            elapsed,
            usage.prompt_tokens if usage else None,
            usage.completion_tokens if usage else None,
            choice.finish_reason,
        )

        if choice.finish_reason == "length":
            logger.warning(
                "Response truncated: model=%s hit the token limit", self.model
            )

        return choice
