#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}    SYAPA KING BOT LAUNCHER${NC}"
echo -e "${GREEN}========================================${NC}"

# Check Python version
echo -e "${YELLOW}Checking Python version...${NC}"
python_version=$(python3 --version 2>&1 | grep -Po '(?<=Python )\d+\.\d+')
if [[ $(echo "$python_version < 3.8" | bc) -eq 1 ]]; then
    echo -e "${RED}Error: Python 3.8 or higher is required${NC}"
    exit 1
fi
echo -e "${GREEN}Python $python_version found${NC}"

# Install system dependencies (if running as root)
if [ "$EUID" -eq 0 ]; then
    echo -e "${YELLOW}Installing system dependencies...${NC}"
    apt-get update
    xargs -a packages.txt apt-get install -y
fi

# Create virtual environment
echo -e "${YELLOW}Setting up virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install Python dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
pip install --upgrade pip
pip install -r requirements.txt

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Creating .env file from example...${NC}"
    cp .env.example .env
    echo -e "${RED}Please edit .env file with your configuration${NC}"
fi

# Create data directory
mkdir -p data

# Run the bot
echo -e "${GREEN}Starting SYAPA BOT...${NC}"
python telegram_bot.py