# config.py
import os
from dotenv import load_dotenv

# Load .env file locally (Render पर effect नहीं होगा, वहां Dashboard में env vars set होंगे)
load_dotenv()

# ✅ Required Environment Variables
REQUIRED_VARS = ["BOT_TOKEN", "API_ID", "API_HASH"]
missing_vars = [var for var in REQUIRED_VARS if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(
        f"❌ Missing required environment variables: {', '.join(missing_vars)}"
    )

BOT_TOKEN = os.getenv("BOT_TOKEN").strip()
API_ID = int(os.getenv("API_ID").strip())
API_HASH = os.getenv("API_HASH").strip()

# ✅ Optional Environment Variables (defaults already filled)
MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb+srv://tejaschavan1110:15HNqpSmaq40eQzX@cluster0.aoldz.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
)
DB_NAME = os.getenv("DB_NAME", "monitor_bot_db")

SOURCE_CHANNELS = [
    int(x) for x in os.getenv("SOURCE_CHANNELS", "-1002487065354").split(",") if x
]
STORE_CHANNEL_ID = int(os.getenv("STORE_CHANNEL_ID", "-1002176533426"))
OUTPUT_CHANNEL_ID = int(os.getenv("OUTPUT_CHANNEL_ID", "-1002271035070"))
LEECH_GROUP_IDS = [
    int(x) for x in os.getenv("LEECH_GROUP_IDS", "-1001944752172").split(",") if x
]

LINK_RESOLVER_API = os.getenv(
    "LINK_RESOLVER_API",
    "https://render-api-1-t692.onrender.com/"
)

MAX_GLOBAL_CONCURRENT_DOWNLOADS = int(
    os.getenv("MAX_GLOBAL_CONCURRENT_DOWNLOADS", "6")
)
MAX_CONCURRENT_PER_GROUP = int(os.getenv("MAX_CONCURRENT_PER_GROUP", "5"))
DOWNLOAD_CHUNK_SIZE = int(os.getenv("DOWNLOAD_CHUNK_SIZE", "524288"))

TMP_DIR = os.getenv("TMP_DIR", "./tmp")

# ✅ Debug print to confirm env loading (will show only safe vars)
print(f"[CONFIG] BOT_TOKEN loaded: {'Yes' if BOT_TOKEN else 'No'}")
print(f"[CONFIG] API_ID: {API_ID}, API_HASH loaded: {'Yes' if API_HASH else 'No'}")
