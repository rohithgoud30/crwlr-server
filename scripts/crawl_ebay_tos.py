import requests
import json
import argparse

# Define the API endpoint URL (adjust if your server runs elsewhere)
API_URL = "http://127.0.0.1:8000/api/v1/crawl-tos"

def crawl_website_tos(target_url: str):
    """
    Sends a request to the crawl-tos API endpoint for the given URL.
    
    Args:
        target_url: The URL of the website to crawl (e.g., ebay.com).
    """
    print(f"Sending request to crawl Terms of Service for: {target_url}")
    
    payload = {"url": target_url}
    # Define the API key header
    api_key = "6e878bf1-c92d-4ba1-99c9-50e3343efd5d"
    headers = {"X-API-KEY": api_key}
    
    try:
        # Include headers in the request
        response = requests.post(API_URL, json=payload, headers=headers, timeout=300) # Increased timeout for potentially long process
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        
        print("Request successful!")
        print("Response:")
        # Pretty print the JSON response
        try:
            print(json.dumps(response.json(), indent=2))
        except json.JSONDecodeError:
            print("Response is not valid JSON:")
            print(response.text)
            
    except requests.exceptions.RequestException as e:
        print(f"An error occurred during the request: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    # Setup argument parser (optional, but good practice)
    parser = argparse.ArgumentParser(description="Crawl the Terms of Service for a given website.")
    parser.add_argument(
        "--url", 
        type=str, 
        default="ebay.com", 
        help="The target website URL (default: ebay.com)"
    )
    args = parser.parse_args()
    
    crawl_website_tos(args.url) 