import os
from dotenv import load_dotenv

load_dotenv()

# ⚠️ Agar .env nahi bana, yahan direct daalo. Warna .env use karo.
BOT_TOKEN = os.getenv("BOT_TOKEN", "9016141137:AAEPjUB6c69vle9CboVz-o6iTMeK21zD1PQ")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "9093552718:AAGgg9jVbMnAwltzIcX2T80mCTLBtwd-W_A")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "8704613141").split(",")]

CHANNEL = "@zyksufirst"
DEVELOPER = "@zyksu"

FAKE_DOMAINS = ["hi2.in", "telegmail.com"]

# Points
POINTS_PER_REFERRAL = 10
POINTS_PER_CHECK = 1

# Tokens
TOKENS_PER_REFERRAL = 50
TOKENS_DAILY_LEADERBOARD = 250
TOKENS_DAILY_FREE = 350

# SMTP settings
SMTP_TIMEOUT = 4
SMTP_SENDER = "noreply@google.com"
MX_CACHE_TTL = 3600
CHECK_CONCURRENCY = 8

# Force Join
FORCE_JOIN = True
REQUIRED_CHANNELS = ["@zyksufirst"]

# Anti-spam
RATE_LIMIT_SECONDS = 2
