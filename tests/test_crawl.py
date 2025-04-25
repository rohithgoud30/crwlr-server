import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
import json

from app.main import app
from app.models.crawl import CrawlTosRequest, CrawlPrivacyRequest
from app.models.extract import ExtractRequest, ExtractResponse
from app.models.textmining import TextMiningResults

client = TestClient(app)

# Mock data
MOCK_TOS_URL = "https://example.com/terms"
MOCK_PP_URL = "https://example.com/privacy"
MOCK_EXTRACTED_TEXT = "This is the extracted text for testing purposes."
MOCK_ONE_SENTENCE_SUMMARY = "This is a one sentence summary."
MOCK_HUNDRED_WORD_SUMMARY = "This is a hundred word summary that is shorter for testing."
MOCK_DOCUMENT_ID = "12345678-1234-5678-1234-567812345678"

# Test for crawl-tos endpoint
@pytest.mark.asyncio
@patch('app.api.v1.endpoints.crawl.find_tos_url')
@patch('app.api.v1.endpoints.crawl.extract_text_from_url')
@patch('app.api.v1.endpoints.crawl.generate_one_sentence_summary')
@patch('app.api.v1.endpoints.crawl.generate_hundred_word_summary')
@patch('app.api.v1.endpoints.crawl.get_word_frequencies')
@patch('app.api.v1.endpoints.crawl.extract_text_mining_metrics')
@patch('app.api.v1.endpoints.crawl.save_document_to_db')
async def test_crawl_tos(
    mock_save_document, 
    mock_extract_metrics, 
    mock_get_frequencies, 
    mock_hundred_summary, 
    mock_one_summary, 
    mock_extract_text, 
    mock_find_tos
):
    # Setup mocks for async functions
    mock_find_tos.return_value = MOCK_TOS_URL
    mock_extract_text.return_value = MOCK_EXTRACTED_TEXT
    mock_one_summary.return_value = MOCK_ONE_SENTENCE_SUMMARY
    mock_hundred_summary.return_value = MOCK_HUNDRED_WORD_SUMMARY
    mock_get_frequencies.return_value = []
    mock_extract_metrics.return_value = TextMiningResults(
        word_count=100,
        avg_word_length=5.0,
        sentence_count=10,
        avg_sentence_length=10.0,
        readability_score=50.0,
        readability_interpretation="Standard",
        unique_word_ratio=0.8,
        capital_letter_freq=0.1,
        punctuation_density=0.05,
        question_frequency=0.02,
        paragraph_count=5,
        common_word_percentage=0.6
    )
    mock_save_document.return_value = MOCK_DOCUMENT_ID
    
    # Create request
    request_data = {"url": "https://example.com"}
    
    # Call the endpoint
    response = client.post("/api/v1/crawl-tos", json=request_data)
    
    # Check the response
    assert response.status_code == 200
    data = response.json()
    assert data["url"] == "https://example.com"
    assert data["tos_url"] == MOCK_TOS_URL
    assert data["success"] is True
    assert data["one_sentence_summary"] == MOCK_ONE_SENTENCE_SUMMARY
    assert data["hundred_word_summary"] == MOCK_HUNDRED_WORD_SUMMARY
    assert data["document_id"] == MOCK_DOCUMENT_ID

# Test for crawl-pp endpoint
@pytest.mark.asyncio
@patch('app.api.v1.endpoints.crawl.find_privacy_policy_url')
@patch('app.api.v1.endpoints.crawl.extract_text_from_url')
@patch('app.api.v1.endpoints.crawl.generate_one_sentence_summary')
@patch('app.api.v1.endpoints.crawl.generate_hundred_word_summary')
@patch('app.api.v1.endpoints.crawl.get_word_frequencies')
@patch('app.api.v1.endpoints.crawl.extract_text_mining_metrics')
@patch('app.api.v1.endpoints.crawl.save_document_to_db')
async def test_crawl_privacy_policy(
    mock_save_document,
    mock_extract_metrics,
    mock_get_frequencies,
    mock_hundred_summary,
    mock_one_summary,
    mock_extract_text,
    mock_find_pp
):
    # Setup mocks for async functions
    mock_find_pp.return_value = MOCK_PP_URL
    mock_extract_text.return_value = MOCK_EXTRACTED_TEXT
    mock_one_summary.return_value = MOCK_ONE_SENTENCE_SUMMARY
    mock_hundred_summary.return_value = MOCK_HUNDRED_WORD_SUMMARY
    mock_get_frequencies.return_value = []
    mock_extract_metrics.return_value = TextMiningResults(
        word_count=100,
        avg_word_length=5.0,
        sentence_count=10,
        avg_sentence_length=10.0,
        readability_score=50.0,
        readability_interpretation="Standard",
        unique_word_ratio=0.8,
        capital_letter_freq=0.1,
        punctuation_density=0.05,
        question_frequency=0.02,
        paragraph_count=5,
        common_word_percentage=0.6
    )
    mock_save_document.return_value = MOCK_DOCUMENT_ID
    
    # Create request
    request_data = {"url": "https://example.com"}
    
    # Call the endpoint
    response = client.post("/api/v1/crawl-pp", json=request_data)
    
    # Check the response
    assert response.status_code == 200
    data = response.json()
    assert data["url"] == "https://example.com"
    assert data["pp_url"] == MOCK_PP_URL
    assert data["success"] is True
    assert data["one_sentence_summary"] == MOCK_ONE_SENTENCE_SUMMARY
    assert data["hundred_word_summary"] == MOCK_HUNDRED_WORD_SUMMARY
    assert data["document_id"] == MOCK_DOCUMENT_ID 