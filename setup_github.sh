#!/bin/bash
# Setup script for Flight Deal Finder GitHub Repository

echo "🚀 Flight Deal Finder - GitHub Setup"
echo "====================================="
echo ""
echo "This script will help you create the GitHub repository and configure secrets."
echo ""
echo "Prerequisites:"
echo "  - GitHub account (rajjj4u)"
echo "  - GitHub CLI installed (gh) or create repo manually"
echo "  - Discord webhook URL ready"
echo ""
read -p "Press Enter to continue..."

# Check if gh CLI is available
if ! command -v gh &> /dev/null; then
    echo "⚠️  GitHub CLI (gh) not found."
    echo "   Install it: brew install gh"
    echo "   Or create the repo manually at https://github.com/new"
    echo ""
    echo "Manual steps:"
    echo "1. Go to https://github.com/new"
    echo "2. Create repo: rajjj4u/flight-deals (public)"
    echo "3. Don't initialize with README/license/.gitignore"
    echo "4. Then run: git remote add origin https://github.com/rajjj4u/flight-deals.git"
    echo "5. git push -u origin main"
    exit 1
fi

# Authenticate if needed
gh auth status 2>/dev/null || gh auth login

# Create repository
echo "📦 Creating GitHub repository..."
gh repo create rajjj4u/flight-deals --public --source=/Users/rajusiripuram/.hermes/scripts/flight_deals --push

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Repository created and code pushed!"
    echo ""
    echo "🔐 Now add the Discord webhook as a GitHub Secret:"
    echo ""
    echo "Option 1: Via CLI (recommended)"
    echo "  gh secret set DISCORD_WEBHOOK_URL --repo rajjj4u/flight-deals"
    echo "  (Enter your webhook URL when prompted)"
    echo ""
    echo "Option 2: Via Web UI"
    echo "  1. Go to: https://github.com/rajjj4u/flight-deals/settings/secrets/actions"
    echo "  2. Click 'New repository secret'"
    echo "  3. Name: DISCORD_WEBHOOK_URL"
    echo "  4. Value: [your Discord webhook URL]"
    echo "  5. Click 'Add secret'"
    echo ""
    echo "✅ After adding the secret, the workflow will run automatically daily at 9 AM UTC."
    echo "   You can also trigger it manually at: https://github.com/rajjj4u/flight-deals/actions"
else
    echo "❌ Failed to create repository. Try manually."
fi