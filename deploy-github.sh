#!/bin/bash
set -e

# This script helps trigger a GitHub Actions workflow deployment

BRANCH=$(git branch --show-current)

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