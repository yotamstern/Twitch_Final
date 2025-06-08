# database.py
import logging
import datetime
import bcrypt

from bson.objectid import ObjectId
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s (database): %(message)s",
    handlers=[logging.FileHandler("database.log"), logging.StreamHandler()],
)


class DatabaseManager:
    """Handles all interactions with the MongoDB database."""

    def __init__(self, mongo_uri="mongodb://localhost:27017/",
                 db_name="LiveStreamApp"):
        """
        Initializes the database connection and collections.
        """
        self.client = None
        self.db = None
        self.users_collection = None
        self.streams_collection = None
        self.is_connected = False

        try:
            logging.info(f"Connecting to MongoDB at {mongo_uri}...")
            self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            self.client.admin.command('hello')  # Check connection
            self.db = self.client[db_name]
            self.users_collection = self.db["users"]
            self.streams_collection = self.db["streams"]
            self.is_connected = True
            logging.info(
                f"Successfully connected to MongoDB database '{db_name}'!")
            self._ensure_indexes()
        except ConnectionFailure as e:
            logging.critical(
                f"MongoDB connection failed: {e}. Check MongoDB service.")
        except Exception as e:
            logging.critical(f"An unexpected error during MongoDB setup: {e}")

    def _ensure_indexes(self):
        """Creates unique indexes for collections if they don't exist."""
        if not self.is_connected:
            return
        try:
            self.users_collection.create_index("username", unique=True)
            self.streams_collection.create_index("host_username")
            logging.info("Database indexes ensured.")
        except OperationFailure as idx_e:
            logging.warning(
                f"Could not ensure indexes (might require permissions): "
                f"{idx_e}")

    def register_user(self, username, password, verify_pass):
        """Registers a new user in the database."""
        if not self.is_connected:
            logging.error("DB Error: Cannot register user (no connection).")
            return "Error: Database connection failed!"
        if not username or not password:
            return "Username and password are required."
        if password != verify_pass:
            return "Passwords do not match!"

        try:
            if self.users_collection.find_one({"username": username}):
                logging.warning(f"Reg fail: User '{username}' exists.")
                return "Username already exists!"

            hashed_pw = bcrypt.hashpw(password.encode('utf-8'),
                                      bcrypt.gensalt())
            user_doc = {"username": username, "password_hash": hashed_pw}
            result = self.users_collection.insert_one(user_doc)
            logging.info(
                f"User '{username}' registered (ID: {result.inserted_id}).")
            return f"User {username} registered successfully!"
        except OperationFailure as e:
            logging.error(
                f"DB op fail during registration for '{username}': {e}")
            return f"Registration database error: {e}"
        except Exception as e:
            logging.exception(
                f"Unexpected error during registration for '{username}': {e}")
            return f"Unexpected registration error: {e}"

    def verify_user_login(self, username, password):
        """Verifies user credentials against the database."""
        if not self.is_connected:
            logging.error("DB Error: Cannot verify user (no connection).")
            return False, "Error: Database connection failed!"
        if not username or not password:
            return False, "Username and password are required."

        try:
            user = self.users_collection.find_one({"username": username})
            if user and bcrypt.checkpw(password.encode('utf-8'),
                                       user.get("password_hash", b'')):
                logging.info(f"User '{username}' logged in successfully.")
                return True, "Login successful!"
            else:
                logging.warning(
                    f"Login failed for user '{username}': Invalid "
                    f"credentials.")
                return False, "Invalid username or password!"
        except Exception as e:
            logging.exception(
                f"Unexpected error during login for '{username}': {e}")
            return False, f"Login error: {e}"

    def create_stream_record(self, host_username, stream_id):
        """Creates or updates a record for a stream when it goes live."""
        if not self.is_connected:
            logging.error(
                "DB Error: Cannot create stream record (no connection).")
            return False, "Database connection failed!"

        try:
            user = self.users_collection.find_one({"username": host_username})
            if not user:
                logging.warning(
                    f"Cannot create stream: Host '{host_username}' not found.")
                return False, "Host user not found!"

            stream_doc = {
                "stream_id": stream_id,
                "host_user_id": user["_id"],
                "host_username": host_username,
                "status": "active",
                "start_time": datetime.datetime.utcnow(),
                "viewer_count": 0,
            }
            self.streams_collection.update_one(
                {"stream_id": stream_id},
                {"$set": stream_doc},
                upsert=True
            )
            logging.info(
                f"Stream record created/updated for ID '{stream_id}' by host "
                f"'{host_username}'.")
            return True, "Stream record updated."
        except Exception as e:
            logging.exception(
                f"Error creating/updating stream record for '{stream_id}': {e}")
            return False, f"Error managing stream record: {e}"

    def set_stream_inactive(self, stream_id):
        """Marks a stream as inactive in the database."""
        if not self.is_connected:
            logging.error(f"DB Error: Cannot set stream {stream_id} inactive.")
            return False

        try:
            result = self.streams_collection.update_one(
                {"stream_id": stream_id},
                {"$set": {
                    "status": "inactive",
                    "end_time": datetime.datetime.utcnow()}}
            )
            if result.modified_count > 0:
                logging.info(f"Stream record '{stream_id}' marked inactive.")
                return True
            else:
                logging.warning(
                    f"Stream record '{stream_id}' not found or already "
                    f"inactive.")
                return False
        except Exception as e:
            logging.exception(
                f"Error setting stream '{stream_id}' inactive: {e}")
            return False


# --- Singleton instance for use across the application ---
db_manager = DatabaseManager()
