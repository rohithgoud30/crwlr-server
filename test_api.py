import requests

# API settings
API_URL = "http://localhost:8000/api/v1/tos"
API_KEY = "6e878bf1-c92d-4ba1-99c9-50e3343efd5d"  # From your .env file

# Test payload
payload = {
    "url": "https://facebook.com"
}

# Headers with the API key
headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

# Make the request
try:
    response = requests.post(API_URL, json=payload, headers=headers)
    
    # Print response details
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")
except Exception as e:
    print(f"Error: {e}") 