FROM python:3.10-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TZ=Asia/Karachi

# Install system dependencies
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    build-essential \
    python3-dev \
    libssl-dev \
    libffi-dev \
    libnss3 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    libgbm1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libxkbcommon0 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create volume for database
VOLUME ["/app/data"]

# Expose port
EXPOSE 4000

# Run the bot
CMD ["python", "telegram_bot.py"]