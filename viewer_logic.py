# viewer_logic.py

import logging
import pickle
import socket
import struct
import threading
import time
import uuid
import json

try:
    import cv2
    import numpy as np
    import pyaudio

    LIBS_AVAILABLE = True
except ImportError:
    logging.error("OpenCV, NumPy or PyAudio not found. Viewer cannot run.")
    LIBS_AVAILABLE = False

from protocol_recv import receive_data

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s (%(threadName)s): %(message)s",
    handlers=[
        logging.FileHandler("viewer_logic.log"),
        logging.StreamHandler(),
    ],
)

# --- Constants ---
AUDIO_FORMAT = pyaudio.paInt16 if LIBS_AVAILABLE else None
CHANNELS = 1
RATE = 16000
CHUNK = 1024
WINDOW_TITLE = "Live Stream Viewer"

# --- Global State ---
viewer_active = threading.Event()
_current_video_socket = None
_current_audio_socket = None
_exit_reason = "The host has ended the stream."
_exit_lock = threading.Lock()


def receive_and_display_video(video_socket, master_tk_root, on_exit_callback):
    """Receives video packets, decodes, and displays them in fullscreen."""
    global _current_video_socket
    _current_video_socket = video_socket
    with _exit_lock:
        global _exit_reason
        _exit_reason = "The host has ended the stream."

    # Aesthetic text properties for "q: Exit"
    text_to_display = "Press 'q' to exit"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.6
    font_color = (210, 210, 210)  # Light grey
    background_color = (40, 40, 40) # Dark grey background for text
    thickness = 1
    line_type = cv2.LINE_AA
    margin = 15  # Margin from the edges of the screen
    bg_padding = 5 # Padding for the background rectangle around the text

    try:
        cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(WINDOW_TITLE, cv2.WND_PROP_FULLSCREEN,
                              cv2.WINDOW_FULLSCREEN)

        while viewer_active.is_set():
            try:
                _, data_payload = receive_data(video_socket)
                if data_payload is None:
                    break
                _, frame_data = pickle.loads(data_payload)
                frame = cv2.imdecode(np.frombuffer(frame_data, np.uint8),
                                     cv2.IMREAD_COLOR)
                if frame is None:
                    continue

                if cv2.getWindowProperty(WINDOW_TITLE,
                                         cv2.WND_PROP_VISIBLE) < 1:
                    with _exit_lock:
                        _exit_reason = "You have left the stream."
                    break

                # Get frame dimensions
                (h, w) = frame.shape[:2]

                # Calculate text size
                (text_w, text_h), baseline = cv2.getTextSize(
                    text_to_display, font, font_scale, thickness
                )

                # Position text at the bottom-left corner
                # text_x is the x-coordinate of the left-most point of the text
                # text_y is the y-coordinate of the baseline of the text
                text_x = margin
                text_y = h - margin

                # Define background rectangle coordinates
                # Rectangle top-left corner
                rect_x1 = text_x - bg_padding
                rect_y1 = text_y - text_h - baseline - bg_padding
                # Rectangle bottom-right corner
                rect_x2 = text_x + text_w + bg_padding
                rect_y2 = text_y + baseline - (baseline // 2) + bg_padding # Adjust for baseline to fit snugly

                # Draw the background rectangle
                cv2.rectangle(frame, (rect_x1, rect_y1), (rect_x2, rect_y2),
                              background_color, -1) # -1 for filled rectangle

                # Put the text on the frame
                cv2.putText(frame, text_to_display, (text_x, text_y),
                            font, font_scale, font_color, thickness, line_type)

                cv2.imshow(WINDOW_TITLE, frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    with _exit_lock:
                        _exit_reason = "You have left the stream."
                    break

            except (socket.error, pickle.UnpicklingError, EOFError):
                logging.warning("Video stream connection lost.")
                break
    except Exception as e:
        logging.exception(f"Unexpected error in video display: {e}")
    finally:
        viewer_active.clear()
        cv2.destroyAllWindows()
        for _ in range(5):
            cv2.waitKey(1)
        with _exit_lock:
            reason = _exit_reason
        if on_exit_callback:
            on_exit_callback(reason)
        logging.info("Video display thread finished.")


def receive_and_play_audio(audio_socket):
    """Receives audio packets and plays them."""
    global _current_audio_socket
    _current_audio_socket = audio_socket
    audio, stream = None, None
    try:
        audio = pyaudio.PyAudio()
        stream = audio.open(format=AUDIO_FORMAT, channels=CHANNELS, rate=RATE,
                            output=True, frames_per_buffer=CHUNK)
        while viewer_active.is_set():
            try:
                _, data_payload = receive_data(audio_socket)
                if data_payload is None:
                    break
                _, audio_data = pickle.loads(data_payload)
                if stream and viewer_active.is_set():
                    stream.write(audio_data)
            except (socket.error, pickle.UnpicklingError, EOFError):
                logging.warning("Audio stream connection lost.")
                break
    except Exception as e:
        if viewer_active.is_set():
            logging.exception(f"Error in audio playback: {e}")
    finally:
        viewer_active.clear()
        if stream:
            stream.stop_stream()
            stream.close()
        if audio:
            audio.terminate()
        logging.info("Audio playback thread finished.")


def launch_viewer_threads(stream_id, master_tk_root, on_exit_callback,
                          host="127.0.0.1", video_port=12345,
                          audio_port=12346):
    """Connects to server, sends VIEWER intent, and launches stream threads."""
    if not LIBS_AVAILABLE:
        return False, "Required libraries (cv2, pyaudio) are missing."

    client_id = str(uuid.uuid4())
    video_sock, audio_sock = None, None
    try:
        viewer_active.clear()
        viewer_active.set()

        video_sock = socket.create_connection((host, video_port), timeout=10)
        audio_sock = socket.create_connection((host, audio_port), timeout=10)
        video_sock.sendall(client_id.encode("utf-8"))
        audio_sock.sendall(client_id.encode("utf-8"))

        intent = {"action": "VIEWER", "stream_id": stream_id}
        intent_bytes = json.dumps(intent).encode('utf-8')
        video_sock.sendall(len(intent_bytes).to_bytes(4, 'big') + intent_bytes)

        res_len = int.from_bytes(video_sock.recv(4), 'big')
        response = json.loads(video_sock.recv(res_len).decode('utf-8'))

        if response.get("status") != "VIEWER_OK":
            raise ValueError(f"Server rejected join: {response.get('message')}")

        threading.Thread(
            target=receive_and_display_video,
            args=(video_sock, master_tk_root, on_exit_callback),
            name="ViewerVideoThread", daemon=True).start()
        threading.Thread(
            target=receive_and_play_audio, args=(audio_sock,),
            name="ViewerAudioThread", daemon=True).start()
        return True, f"Successfully joined stream {stream_id}"

    except Exception as e:
        viewer_active.clear()
        if video_sock:
            video_sock.close()
        if audio_sock:
            audio_sock.close()
        return False, f"Failed to connect: {e}"


def stop_viewer_streaming():
    """Signals the viewer threads to stop and closes sockets."""
    with _exit_lock:
        global _exit_reason
        _exit_reason = "You have left the stream."
    if viewer_active.is_set():
        viewer_active.clear()
    time.sleep(0.2)
    for sock in [_current_video_socket, _current_audio_socket]:
        if sock:
            try:
                sock.close()
            except (OSError, socket.error):
                pass
    try:
        cv2.destroyAllWindows()
        for _ in range(5):
            cv2.waitKey(1)
    except Exception:
        pass