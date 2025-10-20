import logging
import os
import re
from typing import Optional, Tuple

import httpx
from fastapi import APIRouter

from app.core.config import settings
from app.models.summary import SummaryRequest, SummaryResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


def clean_summary_text(text: str) -> str:
    """Remove formatting artifacts from LLM output."""
    text = re.sub(r'[*`"\']+', '', text)
    text = re.sub(r'\n{2,}', '\n\n', text)
    return text.strip()


def get_google_api_key() -> Optional[str]:
    api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if api_key:
        source = "settings" if settings.GEMINI_API_KEY else "environment"
        logger.info("Gemini API key found via %s.", source)
        if len(api_key) < 20:
            logger.warning("Gemini API key appears short. Double-check the value.")
    else:
        logger.error("Gemini API key not configured.")
    return api_key


def get_zai_api_key() -> Optional[str]:
    api_key = settings.ZAI_API_KEY or os.environ.get("ZAI_API_KEY")
    if api_key:
        source = "settings" if settings.ZAI_API_KEY else "environment"
        logger.info("Z.AI API key found via %s.", source)
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
        model = settings.ZAI_MODEL if provider == "zai" else settings.GOOGLE_SUMMARY_MODEL

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


async def call_google_summary(prompt: str, model: str) -> Tuple[Optional[str], Optional[str]]:
    api_key = get_google_api_key()
    if not api_key:
        return None, "Gemini API key not configured"

    model_name = model or settings.GOOGLE_SUMMARY_MODEL
    api_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        async with httpx.AsyncClient() as client:
            logger.info("Calling Google Gemini model '%s'", model_name)
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


async def call_zai_summary(prompt: str, model: str) -> Tuple[Optional[str], Optional[str]]:
    api_key = get_zai_api_key()
    if not api_key:
        return None, "Z.AI API key not configured"

    base_url = (settings.ZAI_BASE_URL or "https://api.z.ai/api/coding/paas/v4").rstrip("/")
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model or settings.ZAI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that writes concise policy summaries."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient() as client:
            logger.info("Calling Z.AI model '%s' at %s", payload["model"], base_url)
            response = await client.post(url, headers=headers, json=payload, timeout=60.0)
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


def build_summary_prompt(content: str, document_type: str, company_name: Optional[str]) -> str:
    if document_type == "pp":
        doc_type_full = "Privacy Policy"
    elif document_type == "tos":
        doc_type_full = "Terms of Service"
    else:
        doc_type_full = document_type

    company_reference = f" for {company_name}" if company_name else ""

    return f"""100-WORD SUMMARY

Write a concise, factual 100-word summary of the {doc_type_full}{company_reference}. Focus on the company policies and practices without referencing external services, other companies, or general industry practices.

Requirements:
- Exactly 100 words (±10)
- Single paragraph
- Objective, factual tone
- No personal pronouns (I, we, you)
- No meta-references (e.g., 'this document', 'this text', 'this policy')
- No conditional language (e.g., 'may', 'might', 'could')
- No links or external references

Provide a direct, factual, and company-specific summary.


ONE-SENTENCE SUMMARY

Write a single sentence (maximum 40 words) summarizing the most important aspect of the {doc_type_full}{company_reference}. Focus on the company policies and practices without referencing external services, other companies, or general industry practices.

Requirements:
- One clear, direct sentence
- Maximum 40 words
- Objective, factual tone
- No personal pronouns (I, we, you)
- No meta-references (e.g., 'this document', 'this text')
- No conditional language (e.g., 'may', 'might', 'could')
- No links or external references


Here is the document content:

{content}
"""


@router.post("/summary", response_model=SummaryResponse)
async def generate_summary(request: SummaryRequest) -> SummaryResponse:
    try:
        text = request.text
        base_url = request.url or ""
        document_type = request.document_type or "tos"
        extraction_success = True
        extraction_message = ""

        if request.extract_response:
            extraction_success = request.extract_response.success
            extraction_message = request.extract_response.message
            if request.extract_response.text:
                text = request.extract_response.text
            if request.extract_response.document_type:
                document_type = request.extract_response.document_type
            if request.extract_response.url:
                base_url = request.extract_response.url

        if not extraction_success:
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                provider=None,
                model=None,
                success=False,
                message=f"Unable to generate summary: {extraction_message}",
                one_sentence_summary=None,
                hundred_word_summary=None,
            )

        if text and is_likely_bot_verification_text(text):
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                provider=None,
                model=None,
                success=False,
                message="Unable to generate summary: Bot verification page detected - unable to access actual content",
                one_sentence_summary=None,
                hundred_word_summary=None,
            )

        if not text or len(text.strip()) < 100:
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                provider=None,
                model=None,
                success=True,
                message="Text extraction succeeded but not enough content for summarisation",
                one_sentence_summary=None,
                hundred_word_summary=None,
            )

        provider, model_name = resolve_provider_and_model(request.provider, request.model)
        prompt = build_summary_prompt(text, document_type, request.company_name)

        logger.info("Generating summary using provider '%s' and model '%s'", provider, model_name)

        if provider == "google":
            summary_text, error = await call_google_summary(prompt, model_name)
        elif provider == "zai":
            summary_text, error = await call_zai_summary(prompt, model_name)
        else:
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                provider=provider,
                model=model_name,
                success=False,
                message=f"Unsupported summary provider '{provider}'",
                one_sentence_summary=None,
                hundred_word_summary=None,
            )

        if summary_text is None:
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                provider=provider,
                model=model_name,
                success=True,
                message=f"Text extraction succeeded but summarisation failed: {error}",
                one_sentence_summary=None,
                hundred_word_summary=None,
            )

        hundred_word_summary, one_sentence_summary = extract_summaries(summary_text)

        return SummaryResponse(
            url=base_url,
            document_type=document_type,
            provider=provider,
            model=model_name,
            one_sentence_summary=one_sentence_summary,
            hundred_word_summary=hundred_word_summary,
            success=True,
            message="Successfully generated summaries",
        )

    except Exception as exc:
        logger.error("Unexpected error during summarisation: %s", exc)
        return SummaryResponse(
            url=request.url or "",
            document_type=request.document_type or "tos",
            provider=None,
            model=None,
            success=False,
            message=f"Error: {exc}",
            one_sentence_summary=None,
            hundred_word_summary=None,
        )


def is_likely_bot_verification_text(text: str) -> bool:
    """Checks if the extracted text is likely from a bot verification page."""
    if not text:
        return False

    text_lower = text.lower()
    word_count = len(text.split())

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

    if word_count < 200 and any(phrase in text_lower for phrase in verification_phrases):
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
        indicators = [indicator for indicator in bot_indicators if indicator in text_lower]

        if matches and indicators:
            logger.warning("Bot verification content detected. Phrases: %s, Indicators: %s", matches, indicators)
            return True

    return False
