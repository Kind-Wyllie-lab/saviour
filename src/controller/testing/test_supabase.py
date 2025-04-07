import os 
from dotenv import load_dotenv
load_dotenv()
import supabase
import logging
import uuid
import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase_client = supabase.create_client(supabase_url, supabase_key)

logger.info(f"Supabase URL: {supabase_url}")
logger.info(f"Supabase Key: {supabase_key}")

# Get all data from controller_test table
logger.info("Getting all data from controller_test table")
response = supabase_client.table("controller_test").select("*").execute()
logger.info(f"Response: {response}")

# Insert data into controller_test table
logger.info("Inserting data into controller_test table")
try: 
    response = supabase_client.table("controller_test").insert({
        # "id": None,
        "type": "test",
        "value": "Test entry inserted from test_supabase.py script at " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }).execute()
    logger.info(f"Response: {response}")
except Exception as e:
    logger.error(f"Error inserting data into controller_test table: {e}")
