import requests

# API settings
API_URL = "http://localhost:8001/api/v1/tos"  # Updated to port 8001
API_KEY = "6e878bf1-c92d-4ba1-99c9-50e3343efd5d"  # API key from test_api.py

# Test websites
test_sites = [
    "https://facebook.com",
    "https://amazon.com",
    "https://github.com",
    "https://twitter.com"
]

# Headers with the API key
headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

print("Testing ToS detection API with optimized user/customer terms scoring...")
print(f"Using API key: {API_KEY}")
print("Running tests on multiple sites to verify optimization improvements...\n")

# Make requests for each test site
for site in test_sites:
    print(f"Testing site: {site}")
    payload = {"url": site}
    
    try:
        response = requests.post(API_URL, json=payload, headers=headers)
        
        # Print response details
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print(f"ToS URL: {result.get('url', 'Not found')}")
            print(f"Verified: {result.get('verified', False)}")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Error: {e}")
    
    print("-" * 50)

print("\nTesting complete!") 