# database.py
import bcrypt
import logging

def register_user(self, username, password, verify_pass):
    """Registers a new user in the database."""
    ## --- חלק א': בדיקות תקינות קלט ראשוניות ---
    if not self.is_connected:
        logging.error("DB Error: Cannot register user (no connection).")
        return "Error: Database connection failed!"
    if not username or not password:
        return "Username and password are required."
    if password != verify_pass:
        return "Passwords do not match!"

    try:
        ## --- חלק ב': בדיקה האם שם המשתמש כבר קיים במסד הנתונים ---
        # שימוש ב-find_one יעיל יותר מ-find לקבלת מסמך בודד.
        if self.users_collection.find_one({"username": username}):
            logging.warning(f"Reg fail: User '{username}' exists.")
            return "Username already exists!"

        ## --- חלק ג': גיבוב הסיסמה באמצעות bcrypt ---
        # 1. מקודדים את הסיסמה ממחרוזת למערך בתים בקידוד utf-8.
        # 2. קוראים לפונקציה bcrypt.gensalt() שמייצרת "מלח" אקראי.
        # 3. הפונקציה bcrypt.hashpw מבצעת את הגיבוב האיטי ומטמיעה את המלח בתוצאה.
        hashed_pw = bcrypt.hashpw(password.encode('utf-8'),
                                  bcrypt.gensalt())

        user_doc = {"username": username, "password_hash": hashed_pw}

        ## --- חלק ד': שמירת המשתמש החדש במסד הנתונים ---
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

# protocol_recv.py
import struct
import socket


def receive_data(client_socket):
    """
    Receives length-prefixed data from a socket.

    The protocol expects a 4-byte unsigned integer (big-endian)
    representing the payload size, followed by the payload itself.
    """
    try:
        ## --- שלב א': קבלת קידומת האורך (4 בתים) ---
        # הלולאה מוודאת שנקבל את כל 4 הבתים, גם אם הרשת שלחה אותם בחלקים.
        size_prefix = b''
        while len(size_prefix) < 4:
            # קריאה מה-socket למספר הבתים החסרים
            chunk = client_socket.recv(4 - len(size_prefix))
            if not chunk: # אם החיבור נסגר, chunk יהיה ריק
                return 0, None
            size_prefix += chunk

        ## --- שלב ב': פענוח קידומת האורך ---
        # שימוש במודול struct כדי לפרוק את 4 הבתים למספר שלם (integer).
        # "I" מציין unsigned integer, וסדר הבתים הוא big-endian (רשת).
        # התוצאה היא tuple, לכן ניגשים לאיבר הראשון [0].
        payload_size = struct.unpack(">I", size_prefix)[0]

        ## --- שלב ג': קבלת המטען (payload) ---
        # בדומה לשלב א', לולאה זו מבטיחה שנקבל את כל הבתים של ההודעה,
        # בהתבסס על הגודל שקיבלנו בשלב הקודם.
        data = b''
        while len(data) < payload_size:
            remaining_bytes = payload_size - len(data)
            chunk = client_socket.recv(remaining_bytes)
            if not chunk:
                # החיבור נסגר לפני שכל ההודעה התקבלה.
                return 0, None
            data += chunk

        # החזרת הגודל והמטען שהתקבל
        return payload_size, data

    except (socket.error, struct.error, ConnectionResetError):
        # טיפול בשגיאות רשת נפוצות כניתוק חינני.
        return 0, None