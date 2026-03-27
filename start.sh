#!/bin/bash

# Install chromium and chromedriver for Render
echo "Installing chromium and chromedriver..."
apt-get update
apt-get install -y chromium chromium-driver

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Create necessary directories
mkdir -p /opt/render/project/src/data

# Run the bot
echo "Starting Facebook Automation Bot..."
python bot.py