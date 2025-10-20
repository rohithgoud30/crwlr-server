import logging
import os
import re
from typing import Optional, Tuple

import httpx
from fastapi import APIRouter

from app.core.config import settings

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


def clean_summary_text(text: str) -> str:
    """
    Clean summary text by removing unwanted characters and formatting
    """
    text = re.sub(r'[*"`\']+', "", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def get_google_api_key() -> Optional[str]:
    api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if api_key:
        source = "settings" if settings.GEMINI_API_KEY else "environment"
        logger.info(f"Gemini API Key found via {source}.")
        if len(api_key) < 20:
            logger.warning("Gemini API key appears short. Check formatting.")
    else:
        logger.error("Gemini API key not configured.")
    return api_key


def get_zai_api_key() -> Optional[str]:
    api_key = settings.ZAI_API_KEY or os.environ.get("ZAI_API_KEY")
    if api_key:
        source = "settings" if settings.ZAI_API_KEY else "environment"
        logger.info(f"Z.AI API Key found via {source}.")
    else:
        logger.error("Z.AI API key not configured.")
    return api_key


def resolve_provider_and_model(
    provider_override: Optional[str], model_override: Optional[str]
) -> Tuple[str, str]:
    provider = (provider_override or settings.SUMMARY_PROVIDER or "google").lower()
    model = model_override or settings.SUMMARY_MODEL

    if model:
        lowered = model.lower()
        if lowered.startswith("glm"):
            provider = "zai"
        elif "gemini" in lowered:
            provider = "google"
    else:
        if provider == "zai":
            model = settings.ZAI_MODEL
        else:
            model = settings.GOOGLE_SUMMARY_MODEL

    if provider == "zai" and not model:
        model = settings.ZAI_MODEL
    if provider == "google" and not model:
        model = settings.GOOGLE_SUMMARY_MODEL

    return provider, model


def extract_summaries(summary_text: str) -> Tuple[str, str]:
    hundred_word_start = summary_text.find("100-WORD SUMMARY")
    one_sentence_start = summary_text.find("ONE-SENTENCE SUMMARY")
    if hundred_word_start == -1 or one_sentence_start == -1:
        raise ValueError("Summary text missing expected sections")
    hundred_word = summary_text[hundred_word_start:one_sentence_start].strip()
    hundred_word = "\n".join(hundred_word.split("\n")[1:]).strip()
    one_sentence = summary_text[one_sentence_start:].strip()
    one_sentence = "\n".join(one_sentence.split("\n")[1:]).strip()
    return clean_summary_text(hundred_word), clean_summary_text(one_sentence)


async def call_google_summary(
    prompt: str, model: str
) -> Tuple[Optional[str], Optional[str]]:
    api_key = get_google_api_key()
    if not api_key:
        return None, "Gemini API key not configured"

    model_name = model or settings.GOOGLE_SUMMARY_MODEL
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"Calling Google Gemini model '{model_name}'")
            response = await client.post(
                api_url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=60.0,
            )
            if response.status_code != 200:
                logger.error("Gemini API request failed: %s", response.text)
                return None, f"Gemini API error ({response.status_code})"
            data = response.json()
            summary_text = data["candidates"][0]["content"]["parts"][0]["text"]
            return summary_text, None
    except httpx.HTTPError as exc:
        logger.error("HTTP error during Gemini request: %s", exc)
        return None, f"HTTP error - {exc}"
    except (KeyError, IndexError) as exc:
        logger.error("Error parsing Gemini response: %s", exc)
        return None, "Unexpected Gemini response structure"


async def call_zai_summary(
    prompt: str, model: str
) -> Tuple[Optional[str], Optional[str]]:
    api_key = get_zai_api_key()
    if not api_key:
        return None, "Z.AI API key not configured"

    base_url = (settings.ZAI_BASE_URL or "https://api.z.ai/api/coding/paas/v4").rstrip(
        "/"
    )
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model or settings.ZAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that writes concise policy summaries.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"Calling Z.AI model '{payload['model']}' at {base_url}")
            response = await client.post(
                url, headers=headers, json=payload, timeout=60.0
            )
            if response.status_code != 200:
                logger.error("Z.AI API request failed: %s", response.text)
                return None, f"Z.AI API error ({response.status_code})"
            data = response.json()
            summary_text = data["choices"][0]["message"]["content"]
            return summary_text, None
    except httpx.HTTPError as exc:
        logger.error("HTTP error during Z.AI request: %s", exc)
        return None, f"HTTP error - {exc}"
    except (KeyError, IndexError) as exc:
        logger.error("Error parsing Z.AI response: %s", exc)
        return None, "Unexpected Z.AI response structure"


def is_likely_bot_verification_text(text: str) -> bool:
    """
    Checks if the extracted text is likely from a bot verification page rather than actual content.
    """
    if not text:
        return False

    text_lower = text.lower()
    word_count = len(text.split())

    # Verification keywords that suggest this is a bot check page
    verification_phrases = [
        "verify yourself",
        "verification required",
        "security check",
        "captcha",
        "prove you're human",
        "not a robot",
        "security verification",
        "security measure",
    ]

    # If the text is short and contains verification phrases, it's likely a bot check
    if word_count < 200 and any(
        phrase in text_lower for phrase in verification_phrases
    ):
        # Check for additional bot verification indicators
        bot_indicators = [
            "browser",
            "reload",
            "retry",
            "try again",
            "refresh",
            "access",
            "blocked",
            "temporary",
        ]

        matches = [phrase for phrase in verification_phrases if phrase in text_lower]
        indicators = [
            indicator for indicator in bot_indicators if indicator in text_lower
        ]

        # If we find both verification phrases and indicators, it's very likely a bot page
        if matches and indicators:
            logger.warning(
                f"Bot verification content detected. Phrases: {matches}, Indicators: {indicators}"
            )
            return True

    return False
