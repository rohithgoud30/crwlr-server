import asyncio
import json
import logging
import os
from datetime import datetime
from policy_detector import check_policy_with_playwright, batch_check_policies

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Output directory for saving results
OUTPUT_DIR = "policy_results"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# List of test websites - a diverse mix of different types of sites
TEST_SITES = [
    # Popular services
    "google.com",
    "facebook.com",
    "twitter.com",
    "instagram.com",
    # E-commerce
    "amazon.com",
    "ebay.com",
    "etsy.com",
    "walmart.com",
    # Tech companies
    "microsoft.com",
    "apple.com",
    "ibm.com",
    "oracle.com",
    # Developer platforms
    "github.com",
    "gitlab.com",
    "stackoverflow.com",
    "npmjs.com",
    # Media sites
    "nytimes.com",
    "bbc.com",
    "cnn.com",
    "reddit.com",
    # International sites
    "baidu.com",
    "yandex.ru",
    "rakuten.co.jp",
    "mercadolibre.com",
    # Educational
    "mit.edu",
    "stanford.edu",
    "coursera.org",
    "udemy.com",
    # Government
    "usa.gov",
    "europa.eu",
    "gov.uk",
    "canada.ca",
    # Others
    "wikipedia.org",
    "imdb.com",
    "spotify.com",
    "twitch.tv",
]


async def run_tests():
    """Run tests on all sites and generate a report"""
    start_time = datetime.now()
    logger.info(
        f"Starting policy detection tests for {len(TEST_SITES)} websites at {start_time}"
    )

    # Run tests for all sites
    results = await batch_check_policies(TEST_SITES)

    # Calculate statistics
    total_sites = len(results)
    tos_found = sum(1 for r in results if r.get("tos_link"))
    privacy_found = sum(1 for r in results if r.get("privacy_link"))
    both_found = sum(1 for r in results if r.get("tos_link") and r.get("privacy_link"))
    errors = sum(1 for r in results if "error" in r)

    # Calculate success rates
    tos_rate = (tos_found / total_sites) * 100
    privacy_rate = (privacy_found / total_sites) * 100
    both_rate = (both_found / total_sites) * 100
    error_rate = (errors / total_sites) * 100

    # Generate report
    report = {
        "test_date": start_time.isoformat(),
        "end_time": datetime.now().isoformat(),
        "duration_seconds": (datetime.now() - start_time).total_seconds(),
        "total_sites_tested": total_sites,
        "statistics": {
            "tos_found": tos_found,
            "privacy_found": privacy_found,
            "both_found": both_found,
            "errors": errors,
            "success_rates": {
                "tos_rate": f"{tos_rate:.2f}%",
                "privacy_rate": f"{privacy_rate:.2f}%",
                "both_rate": f"{both_rate:.2f}%",
                "error_rate": f"{error_rate:.2f}%",
            },
        },
        "results": results,
    }

    # Save detailed report
    report_file = os.path.join(
        OUTPUT_DIR, f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


def print_report(report):
    """Print a human-readable version of the test report"""
    print("\n==================================================")
    print("    POLICY DETECTION TEST RESULTS    ")
    print("==================================================")
    print(f"Test run on: {report['test_date']}")
    print(f"Total sites tested: {report['total_sites_tested']}")
    print(f"Test duration: {report['duration_seconds']:.2f} seconds")
    print("\n-----------------")
    print("SUCCESS RATES:")
    print("-----------------")
    print(
        f"Terms of Service found: {report['statistics']['tos_found']} sites ({report['statistics']['success_rates']['tos_rate']})"
    )
    print(
        f"Privacy Policy found: {report['statistics']['privacy_found']} sites ({report['statistics']['success_rates']['privacy_rate']})"
    )
    print(
        f"Both policies found: {report['statistics']['both_found']} sites ({report['statistics']['success_rates']['both_rate']})"
    )
    print(
        f"Sites with errors: {report['statistics']['errors']} sites ({report['statistics']['success_rates']['error_rate']})"
    )

    print("\n-----------------")
    print("DETAILED RESULTS:")
    print("-----------------")

    # Group results by success/partial/failure
    success = []
    partial = []
    failure = []

    for result in report["results"]:
        if "error" in result:
            failure.append(result)
        elif result.get("tos_link") and result.get("privacy_link"):
            success.append(result)
        elif result.get("tos_link") or result.get("privacy_link"):
            partial.append(result)
        else:
            failure.append(result)

    # Print successful results
    print("\n✅ FULLY SUCCESSFUL DETECTIONS:")
    for result in success:
        print(f"  - {result['url']}")
        print(f"    ToS: {result['tos_link']}")
        print(f"    Privacy: {result['privacy_link']}")

    # Print partial results
    print("\n⚠️ PARTIAL DETECTIONS:")
    for result in partial:
        print(f"  - {result['url']}")
        if result.get("tos_link"):
            print(f"    ToS: {result['tos_link']}")
        if result.get("privacy_link"):
            print(f"    Privacy: {result['privacy_link']}")

    # Print failures
    print("\n❌ FAILED DETECTIONS:")
    for result in failure:
        print(f"  - {result['url']}")
        if "error" in result:
            print(f"    Error: {result['error']}")

    print("\n==================================================")
    print(
        f"Detailed report saved to: {os.path.join(OUTPUT_DIR, 'test_report_[timestamp].json')}"
    )
    print("==================================================")


async def main():
    # Run all tests
    report = await run_tests()

    # Print human-readable report
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
