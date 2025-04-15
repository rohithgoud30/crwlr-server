#!/bin/bash
set -e

# This script helps trigger a GitHub Actions workflow deployment

BRANCH=$(git branch --show-current)

# Security check for service account keys in the codebase
if git ls-files | grep -E "\.json$" | grep -i "cogent\-sunspot|service\-account|credentials|-sa|crwlr-server|serviceAccount|gcp-|google-" > /dev/null; then
    echo "⚠️ WARNING: Potential service account key files detected in the repository!"
    echo "Please remove these files immediately and store credentials ONLY as GitHub secrets."
    echo "Check .gitignore to ensure key files are properly excluded."
    read -p "Do you want to continue anyway? (y/n): " SEC_CONFIRM
    if [ "$SEC_CONFIRM" != "y" ]; then
        echo "Aborting deployment."
        exit 1
    fi
fi

# Check for other sensitive files
if git ls-files | grep -E "\.env\.(local|production)|\.pem$|\.key$" > /dev/null; then
    echo "⚠️ WARNING: Potential sensitive files detected in the repository!"
    echo "Please remove these files immediately and store sensitive data securely."
    read -p "Do you want to continue anyway? (y/n): " SEC_CONFIRM
    if [ "$SEC_CONFIRM" != "y" ]; then
        echo "Aborting deployment."
        exit 1
    fi
fi

if [ "$BRANCH" != "main" ]; then
    echo "⚠️ You are not on the main branch (current: $BRANCH)"
    read -p "Do you want to continue anyway? (y/n): " CONFIRM
    if [ "$CONFIRM" != "y" ]; then
        echo "Aborting deployment."
        exit 1
    fi
fi

echo "🔄 Pushing latest changes to GitHub to trigger CI/CD pipeline..."
git push origin $BRANCH

echo "✅ Deployment triggered!"
echo "📊 View deployment status at: https://github.com/rohithgoud30/crwlr-server/actions"
echo "Note: The deployment may take 3-5 minutes to complete." 