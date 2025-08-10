# config.py
import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "7964338386:AAH2puEWEK9IsBN6mIVb0xahkIPu9gkdJYo")
API_ID = int(os.getenv("API_ID", "27710337"))           # optional for user sessions; not used here
API_HASH = os.getenv("API_HASH", "354e1dd8e1e3041ee2145196da8d6aac")             # optional
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://tejaschavan1110:15HNqpSmaq40eQzX@cluster0.aoldz.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "monitor_bot_db")

# Channel / Group IDs (use -100... for channels)
SOURCE_CHANNELS = [int(x) for x in os.getenv("SOURCE_CHANNELS", "-1002487065354").split(",") if x]
STORE_CHANNEL_ID = int(os.getenv("STORE_CHANNEL_ID", "-1002176533426"))  # where bot uploads files to get file_id
OUTPUT_CHANNEL_ID = int(os.getenv("OUTPUT_CHANNEL_ID", "-1002271035070")) # where edited/forwarded final posts go
LEECH_GROUP_IDS = [int(x) for x in os.getenv("LEECH_GROUP_IDS", "-1001944752172").split(",") if x]

# External API to convert terabox -> direct (your provider)
LINK_RESOLVER_API = os.getenv("LINK_RESOLVER_API", "https://render-api-1-t692.onrender.com/")

# Concurrency / limits
MAX_GLOBAL_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_GLOBAL_CONCURRENT_DOWNLOADS", "6"))
MAX_CONCURRENT_PER_GROUP = int(os.getenv("MAX_CONCURRENT_PER_GROUP", "5"))
DOWNLOAD_CHUNK_SIZE = int(os.getenv("DOWNLOAD_CHUNK_SIZE", "524288"))  # 512KB

# temp folder
TMP_DIR = os.getenv("TMP_DIR", "./tmp")
