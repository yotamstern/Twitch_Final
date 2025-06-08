# login_server.py
import socket
import threading
import json
import logging
import ssl
import os

from database import db_manager

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s (login_server): %(message)s",
    handlers=[logging.FileHandler("login_server.log"), logging.StreamHandler()],
)

# --- Server Configuration ---
LOGIN_SERVER_HOST = '0.0.0.0'
LOGIN_SERVER_PORT = 12350
MAX_BUFFER_SIZE = 4096

CERT_FILE = 'server.crt'
KEY_FILE = 'server.key'


class ClientHandler(threading.Thread):
    """Handles a single client connection in its own thread."""

    def __init__(self, ssl_socket, addr, active_users_set, lock):
        super().__init__()
        self.ssl_socket = ssl_socket
        self.addr = addr
        self.active_users = active_users_set
        self.lock = lock
        self.name = f"SecureLoginClient-{addr[0]}-{addr[1]}"

    def _send_response(self, message):
        """Encodes and sends a response to the client."""
        try:
            response_bytes = message.encode('utf-8')
            response_length_prefix = len(response_bytes).to_bytes(4, 'big')
            self.ssl_socket.sendall(response_length_prefix + response_bytes)
            logging.info(f"Sent SSL response to {self.addr}: {message}")
        except socket.error as e:
            logging.error(f"Socket error sending to {self.addr}: {e}")

    def run(self):
        """The main logic for handling a client request."""
        logging.info(f"Accepted SSL connection from {self.addr}")
        try:
            length_prefix = self.ssl_socket.recv(4)
            if not length_prefix:
                logging.warning(
                    f"No length prefix from {self.addr}. Closing.")
                return

            json_length = int.from_bytes(length_prefix, byteorder='big')
            request_json = b""
            while len(request_json) < json_length:
                chunk = self.ssl_socket.recv(
                    min(json_length - len(request_json), MAX_BUFFER_SIZE))
                if not chunk:
                    logging.warning(
                        f"Connection closed by {self.addr} during receive.")
                    return
                request_json += chunk

            request_data = json.loads(request_json.decode('utf-8'))
            logging.info(f"Received SSL request from {self.addr}: "
                         f"{request_data}")

            action = request_data.get("action")
            username = request_data.get("username")
            password = request_data.get("password")

            if action == "LOGIN":
                with self.lock:
                    if username in self.active_users:
                        response = f"ERROR:User '{username}' is already " \
                                   f"logged in."
                    else:
                        success, msg = db_manager.verify_user_login(username,
                                                                    password)
                        if success:
                            response = f"SUCCESS:{msg}"
                            self.active_users.add(username)
                            logging.info(
                                f"User '{username}' logged in. Active users: "
                                f"{list(self.active_users)}")
                        else:
                            response = f"ERROR:{msg}"
                self._send_response(response)

            elif action == "REGISTER":
                confirm = request_data.get("confirm_password")
                message = db_manager.register_user(username, password, confirm)
                if "successfully" in message:
                    response = f"SUCCESS:{message}"
                else:
                    response = f"ERROR:{message}"
                self._send_response(response)

            elif action == "LOGOUT":
                with self.lock:
                    if username in self.active_users:
                        self.active_users.remove(username)
                        logging.info(
                            f"User '{username}' logged out. Active users: "
                            f"{list(self.active_users)}")
                        response = "SUCCESS:Logged out successfully."
                    else:
                        logging.warning(
                            f"Logout for '{username}' not in active set.")
                        response = "SUCCESS:User was not listed as active, " \
                                   f"but logout processed."
                self._send_response(response)

            else:
                self._send_response("ERROR:Invalid action.")

        except json.JSONDecodeError:
            logging.error(f"Invalid JSON received from {self.addr}")
            self._send_response("ERROR:Invalid request format.")
        except (ssl.SSLError, socket.error) as e:
            logging.error(f"Network error with {self.addr}: {e}")
        except Exception as e:
            logging.exception(f"Error handling client {self.addr}: {e}")
            self._send_response("ERROR:An unexpected server error occurred.")
        finally:
            logging.info(f"Closing SSL connection with {self.addr}")
            if self.ssl_socket:
                try:
                    self.ssl_socket.shutdown(socket.SHUT_RDWR)
                except (OSError, socket.error):
                    pass
                self.ssl_socket.close()


def start_login_server():
    """Starts the TCP login server with SSL/TLS encryption."""
    if not db_manager.is_connected:
        logging.critical(
            "DB not connected. Login server cannot start.")
        return

    if not os.path.exists(CERT_FILE) or not os.path.exists(KEY_FILE):
        logging.critical(
            f"Cert ('{CERT_FILE}') or Key ('{KEY_FILE}') not found.")
        return

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    except ssl.SSLError as e:
        logging.critical(f"SSL Error loading certs: {e}. "
                         f"Ensure they are valid and not password-protected.")
        return

    active_users = set()
    active_users_lock = threading.Lock()
    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        raw_socket.bind((LOGIN_SERVER_HOST, LOGIN_SERVER_PORT))
        raw_socket.listen(5)
        ssl_server_socket = context.wrap_socket(raw_socket, server_side=True)
        logging.info(
            f"Secure Login server listening on {LOGIN_SERVER_HOST}:"
            f"{LOGIN_SERVER_PORT} (TLS enabled)")

        while True:
            conn, addr = ssl_server_socket.accept()
            handler = ClientHandler(conn, addr, active_users,
                                    active_users_lock)
            handler.start()

    except OSError as e:
        logging.critical(f"Could not bind to port {LOGIN_SERVER_PORT}: {e}")
    except KeyboardInterrupt:
        logging.info("Login server shutting down.")
    finally:
        if 'ssl_server_socket' in locals():
            ssl_server_socket.close()
        else:
            raw_socket.close()


if __name__ == "__main__":
    start_login_server()