import structlog
import httpx

from app.config import get_settings
from app.core.exceptions import LLMUnavailableError

settings = get_settings()
logger = structlog.get_logger(__name__)

_TIMEOUT = 30.0  # seconds


async def call_llm(prompt: str) -> str:
    """
    Route to the configured LLM provider.
    Raises LLMUnavailableError on any provider failure after retries.
    """
    if settings.llm_provider == "openai":
        return await _call_openai(prompt)
    elif settings.llm_provider == "anthropic":
        return await _call_anthropic(prompt)
    else:
        raise LLMUnavailableError(f"Unknown LLM provider: {settings.llm_provider}")


async def _call_openai(prompt: str) -> str:
    if not settings.openai_api_key:
        raise LLMUnavailableError("OpenAI API key not configured")

    payload = {
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": settings.llm_max_tokens,
        "temperature": settings.llm_temperature,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException:
        logger.error("OpenAI request timed out")
        raise LLMUnavailableError("OpenAI request timed out")
    except httpx.HTTPStatusError as e:
        logger.error("OpenAI HTTP error", status=e.response.status_code)
        raise LLMUnavailableError(f"OpenAI returned {e.response.status_code}")
    except Exception as e:
        logger.error("OpenAI unexpected error", error=str(e))
        raise LLMUnavailableError("OpenAI unavailable")


async def _call_anthropic(prompt: str) -> str:
    if not settings.anthropic_api_key:
        raise LLMUnavailableError("Anthropic API key not configured")

    payload = {
        "model": settings.llm_model,
        "max_tokens": settings.llm_max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"].strip()
    except httpx.TimeoutException:
        logger.error("Anthropic request timed out")
        raise LLMUnavailableError("Anthropic request timed out")
    except httpx.HTTPStatusError as e:
        logger.error("Anthropic HTTP error", status=e.response.status_code)
        raise LLMUnavailableError(f"Anthropic returned {e.response.status_code}")
    except Exception as e:
        logger.error("Anthropic unexpected error", error=str(e))
        raise LLMUnavailableError("Anthropic unavailable")