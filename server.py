# server.py
import logging
import pickle
import queue
import socket
import struct
import threading
import json

from protocol_recv import receive_data

try:
    import cv2
    import numpy as np
    import pyaudio

    PREVIEW_ENABLED_FLAG = True
except ImportError:
    logging.warning("OpenCV or PyAudio not found. Server preview disabled.")
    PREVIEW_ENABLED_FLAG = False

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s (%(threadName)s): %(message)s",
    handlers=[logging.FileHandler("server.log"), logging.StreamHandler()],
)


class StreamServer:
    """Manages all streaming sockets, clients, and active streams."""

    def __init__(self, host='0.0.0.0', video_port=12345, audio_port=12346):
        self.host = host
        self.video_port = video_port
        self.audio_port = audio_port
        self.active_streams = {}
        self.stream_lock = threading.Lock()
        self.pending_pairs = {}
        self.server_running = True

    def start(self):
        """Binds sockets and starts the main server loops."""
        try:
            video_socket = self._create_listening_socket(self.video_port)
            audio_socket = self._create_listening_socket(self.audio_port)
            logging.info(f"Video server on {self.host}:{self.video_port}")
            logging.info(f"Audio server on {self.host}:{self.audio_port}")

            threading.Thread(target=self._connection_loop,
                             args=(video_socket, "video"), daemon=True).start()
            threading.Thread(target=self._connection_loop,
                             args=(audio_socket, "audio"), daemon=True).start()

            logging.info("Server setup complete. Press Ctrl+C to stop.")
            while self.server_running:
                threading.Event().wait(3600)

        except OSError as e:
            logging.critical(f"Failed to bind a port. In use? Error: {e}")
        except KeyboardInterrupt:
            logging.info("Shutdown signal received.")
        finally:
            self.shutdown()

    def _create_listening_socket(self, port):
        """Creates and binds a reusable TCP socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, port))
        sock.listen(10)
        return sock

    def _connection_loop(self, sock, sock_type):
        """Continuously accepts new connections on a given socket."""
        while self.server_running:
            try:
                conn, addr = sock.accept()
                self.handle_incoming_connection(conn, addr, sock_type)
            except socket.error:
                logging.warning(f"Socket error on accept ({sock_type}). "
                                f"Assuming shutdown.")
                break
            except Exception as e:
                if self.server_running:
                    logging.error(f"Error in {sock_type} loop: {e}")

    def handle_incoming_connection(self, sock, addr, sock_type):
        """Pairs sockets and assigns roles (host/viewer) to new clients."""
        logging.info(f"Accepted {sock_type} connection from {addr}")
        try:
            sock.settimeout(10.0)
            client_id = sock.recv(36).decode("utf-8")
            if not client_id or len(client_id) != 36:
                logging.warning(f"{addr} did not send a valid ID. Closing.")
                sock.close()
                return
            sock.settimeout(None)
            logging.info(f"Received ID {client_id} from {addr} ({sock_type})")

            with self.stream_lock:
                self.pending_pairs.setdefault(client_id, {})[sock_type] = sock
                pair = self.pending_pairs[client_id]

            if "video" in pair and "audio" in pair:
                logging.info(f"Client pair complete for ID: {client_id}")
                with self.stream_lock:
                    paired_client = self.pending_pairs.pop(client_id)
                self._assign_client_role(client_id, paired_client)

        except (socket.timeout, ConnectionError) as e:
            logging.warning(f"Connection error with {addr}: {e}")
            sock.close()
        except Exception as e:
            logging.exception(
                f"Error handling incoming {sock_type} from {addr}: {e}")
            sock.close()

    def _assign_client_role(self, client_id, client_sockets):
        """Reads client intent and sets them up as a Host or Viewer."""
        video_sock = client_sockets["video"]
        audio_sock = client_sockets["audio"]
        try:
            video_sock.settimeout(10.0)
            length = int.from_bytes(video_sock.recv(4), byteorder='big')
            intent_data = json.loads(video_sock.recv(length).decode('utf-8'))
            video_sock.settimeout(None)
            action = intent_data.get("action")
            logging.info(f"Client {client_id} has action: '{action}'")

            if action == "HOST":
                self._setup_host(client_id, video_sock, audio_sock)
            elif action == "VIEWER":
                stream_id = intent_data.get("stream_id")
                self._setup_viewer(client_id, stream_id, video_sock,
                                   audio_sock)
            else:
                raise ValueError(f"Invalid action '{action}'")

        except Exception as e:
            logging.error(f"Role assignment failed for {client_id}: {e}")
            video_sock.close()
            audio_sock.close()

    def _setup_host(self, client_id, video_sock, audio_sock):
        """Configures a client as a stream host."""
        stream_id = f"stream_{client_id[:8]}"
        with self.stream_lock:
            self.active_streams[stream_id] = {
                "host": {"id": client_id, "video": video_sock,
                         "audio": audio_sock},
                "viewers": {},
            }
        logging.info(f"Assigned HOST role to {client_id} for {stream_id}")
        confirmation = {"status": "HOST_OK", "stream_id": stream_id}
        conf_bytes = json.dumps(confirmation).encode('utf-8')
        video_sock.sendall(len(conf_bytes).to_bytes(4, 'big') + conf_bytes)

        threading.Thread(target=self.broadcast_media,
                         args=(video_sock, stream_id, "video"),
                         daemon=True).start()
        threading.Thread(target=self.broadcast_media,
                         args=(audio_sock, stream_id, "audio"),
                         daemon=True).start()

    def _setup_viewer(self, client_id, stream_id, video_sock, audio_sock):
        """Configures a client as a stream viewer."""
        with self.stream_lock:
            if stream_id not in self.active_streams:
                rejection = {"status": "ERROR", "message": "Stream not found"}
                logging.warning(
                    f"Viewer {client_id} requested non-existent stream "
                    f"'{stream_id}'.")
            else:
                self.active_streams[stream_id]["viewers"][client_id] = {
                    "video": video_sock, "audio": audio_sock
                }
                confirmation = {"status": "VIEWER_OK"}
                logging.info(f"Assigned VIEWER to {client_id} for {stream_id}")

        response = confirmation if 'confirmation' in locals() else rejection
        res_bytes = json.dumps(response).encode('utf-8')
        video_sock.sendall(len(res_bytes).to_bytes(4, 'big') + res_bytes)
        if response.get("status") == "ERROR":
            video_sock.close()
            audio_sock.close()

    def broadcast_media(self, host_socket, stream_id, media_type):
        """Receives media from host and broadcasts it to all viewers."""
        host_id = self.active_streams.get(stream_id, {}).get("host", {}).get(
            "id")
        while self.server_running:
            try:
                packet_size, data_payload = receive_data(host_socket)
                if data_payload is None:
                    logging.warning(
                        f"Host {host_id} ({media_type}) disconnected for "
                        f"stream {stream_id}.")
                    break

                viewers_to_remove = []
                with self.stream_lock:
                    if stream_id not in self.active_streams:
                        break
                    for vid, v_info in self.active_streams[stream_id][
                        "viewers"].items():
                        try:
                            v_info[media_type].sendall(
                                struct.pack("I", packet_size) + data_payload)
                        except (socket.error, ConnectionError):
                            viewers_to_remove.append(vid)

                if viewers_to_remove:
                    self._remove_viewers(stream_id, viewers_to_remove)

            except (socket.error, struct.error, EOFError):
                logging.warning(
                    f"Connection issue with host {host_id} on {stream_id}.")
                break
            except Exception as e:
                logging.exception(f"Error in broadcast for {host_id}: {e}")
                break

        self.cleanup_stream(stream_id)
        logging.info(
            f"Broadcast_media thread finished for host {host_id}.")

    def _remove_viewers(self, stream_id, viewer_ids):
        """Removes disconnected viewers from a stream's viewer list."""
        with self.stream_lock:
            if stream_id in self.active_streams:
                for viewer_id in viewer_ids:
                    if viewer_id in self.active_streams[stream_id]["viewers"]:
                        logging.info(
                            f"Removing viewer {viewer_id} from {stream_id}")
                        self.active_streams[stream_id]["viewers"].pop(
                            viewer_id)

    def cleanup_stream(self, stream_id):
        """Safely closes all sockets and removes a stream from state."""
        with self.stream_lock:
            if stream_id in self.active_streams:
                logging.info(f"Cleaning up stream {stream_id}...")
                stream = self.active_streams.pop(stream_id)
                for role, member in stream.items():
                    if role == "host":
                        for sock in member.values():
                            if isinstance(sock, socket.socket):
                                sock.close()
                    elif role == "viewers":
                        for viewer in member.values():
                            for sock in viewer.values():
                                if isinstance(sock, socket.socket):
                                    sock.close()
                logging.info(f"Cleanup for stream {stream_id} complete.")

    def shutdown(self):
        """Shuts down the entire server gracefully."""
        self.server_running = False
        with self.stream_lock:
            for stream_id in list(self.active_streams.keys()):
                self.cleanup_stream(stream_id)
        logging.info("Server shutdown complete.")


if __name__ == "__main__":
    server = StreamServer()
    server.start()