# host_streamer.py

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
    logging.error(
        "OpenCV, NumPy or PyAudio not found. Host streaming will fail.")
    LIBS_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s (%(threadName)s): %(message)s",
    handlers=[
        logging.FileHandler("host_streamer.log"),
        logging.StreamHandler(),
    ],
)

# --- Constants ---
AUDIO_FORMAT = pyaudio.paInt16 if LIBS_AVAILABLE else None
CHANNELS = 1
RATE = 16000
CHUNK = 1024
JPEG_QUALITY = 75
FPS_LIMIT = 20

# --- Global State ---
streaming_active = threading.Event()
_on_stream_end_callback = None
_host_video_socket_ref = None
_host_audio_socket_ref = None
_end_stream_button_clicked = False
_mic_muted = False # New global state for microphone mute

def _host_preview_mouse_callback(event, x, y, flags, param):
    """Handles mouse clicks on the 'End Stream' and 'Mute' buttons in the preview."""
    global _end_stream_button_clicked, _mic_muted
    end_btn_rect, mute_btn_rect = param

    if event == cv2.EVENT_LBUTTONDOWN:
        # Check End Stream button
        if end_btn_rect:
            x1, y1, x2, y2 = end_btn_rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                logging.info("'End Stream' button clicked in host preview.")
                _end_stream_button_clicked = True
                return # Consume event

        # Check Mute/Unmute button
        if mute_btn_rect:
            x1, y1, x2, y2 = mute_btn_rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                _mic_muted = not _mic_muted
                logging.info(f"Microphone toggled: {'Muted' if _mic_muted else 'Unmuted'}")
                # No return here, allow other buttons to be checked if needed later

def _draw_control_buttons(frame):
    """Draws clickable 'End Stream' and 'Mute/Unmute' buttons on the frame."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.6
    thick = 1
    pad = 12
    text_color = (255, 255, 255)
    disp_h, disp_w = frame.shape[:2]

    buttons_info = [
        {"text": "End Stream", "bg_color": (30, 30, 200)},
        {"text": "Mute" if not _mic_muted else "Unmute", "bg_color": (200, 30, 30) if _mic_muted else (30, 150, 30)},
    ]

    button_rects = []
    current_x = disp_w - pad
    for btn in reversed(buttons_info): # Draw from right to left
        btn_text = btn["text"]
        bg_color = btn["bg_color"]

        (text_w, text_h), _ = cv2.getTextSize(btn_text, font, scale, thick)
        btn_w = text_w + 2 * pad
        btn_h = text_h + 2 * pad

        x2 = current_x
        x1 = current_x - btn_w
        y1 = pad
        y2 = y1 + btn_h

        cv2.rectangle(frame, (x1, y1), (x2, y2), bg_color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (200, 200, 200), 1)
        text_x = x1 + (btn_w - text_w) // 2
        text_y = y1 + (btn_h + text_h) // 2
        cv2.putText(frame, btn_text, (text_x, text_y), font, scale, text_color,
                    thick, cv2.LINE_AA)

        button_rects.insert(0, (x1, y1, x2, y2)) # Add to the beginning to maintain order
        current_x = x1 - pad # Move left for the next button

    return button_rects

def send_video(video_socket):
    """Captures video, shows a preview, and sends it to the server."""
    global _host_video_socket_ref, _end_stream_button_clicked
    _end_stream_button_clicked = False
    _host_video_socket_ref = video_socket
    cap = None
    preview_window_name = "Host Preview"

    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            raise IOError("Cannot open camera for host.")

        cv2.namedWindow(preview_window_name, cv2.WINDOW_NORMAL)

        # --- Set larger window size and center it ---
        # IMPORTANT: Replace these with your actual screen resolution
        screen_w, screen_h = 1920, 1080  # Example: Full HD screen resolution
        preview_w, preview_h = 1280, 720  # Desired preview window size (e.g., 16:9 aspect ratio)

        cv2.resizeWindow(preview_window_name, preview_w, preview_h)

        # Calculate position to center the window
        x_pos = (screen_w - preview_w) // 2
        y_pos = (screen_h - preview_h) // 2

        # Adjust y_pos slightly up if you want it visually higher than dead center
        # For example, to move it up by 10% of the screen height:
        # y_pos = max(0, y_pos - int(screen_h * 0.10))

        cv2.moveWindow(preview_window_name, x_pos, y_pos)
        # --- End of window sizing and centering ---


        # Initialize button_rects as a tuple of None for the mouse callback
        button_rects = (None, None)
        cv2.setMouseCallback(preview_window_name, _host_preview_mouse_callback,
                             param=button_rects)

        last_frame_time = 0
        while streaming_active.is_set():
            time_to_wait = (1 / FPS_LIMIT) - (time.time() - last_frame_time)
            if time_to_wait > 0:
                time.sleep(time_to_wait)
            last_frame_time = time.time()

            ret, frame = cap.read()
            if not ret:
                logging.warning("Failed to grab frame from camera for host.")
                continue

            # --- Display Preview with Buttons ---
            display_frame = frame.copy()
            button_rects = _draw_control_buttons(display_frame)
            cv2.setMouseCallback(preview_window_name,
                                 _host_preview_mouse_callback, tuple(button_rects))
            cv2.imshow(preview_window_name, display_frame)

            # --- Check for Exit Conditions ---
            key = cv2.waitKey(1) & 0xFF
            if (_end_stream_button_clicked or
                    cv2.getWindowProperty(preview_window_name,
                                          cv2.WND_PROP_VISIBLE) < 1):
                streaming_active.clear()
                break

            # --- Prepare and Send Frame ---
            ret_encode, buffer = cv2.imencode(
                '.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
            if not ret_encode:
                continue
            data_to_send = pickle.dumps((time.time(), buffer))
            video_socket.sendall(
                struct.pack("I", len(data_to_send)) + data_to_send)

    except Exception as e:
        logging.exception(f"Error in host send_video thread: {e}")
    finally:
        streaming_active.clear()
        if cap:
            cap.release()
        cv2.destroyAllWindows()
        for _ in range(5):
            cv2.waitKey(1)
        if _on_stream_end_callback:
            _on_stream_end_callback()
        logging.info("Host video sending and preview thread finished.")


def send_audio(audio_socket):
    """Captures audio and sends it to the server."""
    global _host_audio_socket_ref
    _host_audio_socket_ref = audio_socket
    audio, stream = None, None
    try:
        audio = pyaudio.PyAudio()
        stream = audio.open(format=AUDIO_FORMAT, channels=CHANNELS, rate=RATE,
                            input=True, frames_per_buffer=CHUNK)
        while streaming_active.is_set():
            if _mic_muted:
                # Send silence when muted
                chunk = b'\x00' * CHUNK * 2 # 2 bytes per int16 sample
            else:
                try:
                    chunk = stream.read(CHUNK, exception_on_overflow=False)
                except IOError as e:
                    if e.errno == pyaudio.paInputOverflowed:
                        logging.warning("Audio input overflowed.")
                        chunk = b'\x00' * CHUNK * 2 # Send silence on overflow
                    else:
                        raise

            packed_data = pickle.dumps((time.time(), chunk))
            audio_socket.sendall(
                struct.pack("I", len(packed_data)) + packed_data)
    except Exception as e:
        logging.exception(f"Error in host send_audio thread: {e}")
    finally:
        streaming_active.clear()
        if stream:
            stream.stop_stream()
            stream.close()
        if audio:
            audio.terminate()
        logging.info("Host audio sending thread finished.")


def launch_host_threads(host='127.0.0.1', video_port=12345, audio_port=12346,
                        master_tk_root=None, on_stream_end_callback=None):
    """Connects to server, sends HOST intent, and launches stream threads."""
    global _on_stream_end_callback, _mic_muted
    _on_stream_end_callback = on_stream_end_callback
    _mic_muted = False # Reset mute state on launch

    if not LIBS_AVAILABLE:
        return False, "Required libraries (cv2, pyaudio) missing.", None

    client_id = str(uuid.uuid4())
    video_sock, audio_sock = None, None
    try:
        streaming_active.clear()
        streaming_active.set()

        # Connect sockets
        video_sock = socket.create_connection((host, video_port), timeout=10)
        audio_sock = socket.create_connection((host, audio_port), timeout=10)
        video_sock.sendall(client_id.encode('utf-8'))
        audio_sock.sendall(client_id.encode('utf-8'))

        # Send HOST intent and get confirmation
        intent_payload = {"action": "HOST"}
        intent_bytes = json.dumps(intent_payload).encode('utf-8')
        video_sock.sendall(len(intent_bytes).to_bytes(4, 'big') + intent_bytes)

        response_len = int.from_bytes(video_sock.recv(4), byteorder='big')
        response = json.loads(video_sock.recv(response_len).decode('utf-8'))

        if response.get("status") != "HOST_OK":
            raise ValueError(f"Server denied request: {response.get('message')}")
        stream_id = response.get("stream_id")
        if not stream_id:
            raise ValueError("HOST_OK received but no stream_id.")

        # Launch threads
        threading.Thread(target=send_video, args=(video_sock,),
                         daemon=True, name="HostSendVideo").start()
        threading.Thread(target=send_audio, args=(audio_sock,),
                         daemon=True, name="HostSendAudio").start()
        return True, "Streaming started.", stream_id

    except Exception as e:
        logging.error(f"Host setup failed: {e}")
        streaming_active.clear()
        if video_sock:
            video_sock.close()
        if audio_sock:
            audio_sock.close()
        if on_stream_end_callback:
            on_stream_end_callback()
        return False, f"Failed to connect/confirm: {e}", None


def stop_host_streaming():
    """Signals the streaming threads to stop."""
    logging.info("Requesting host streaming threads to stop.")
    if streaming_active.is_set():
        streaming_active.clear()
    time.sleep(0.2)  # Allow threads to notice the event change
    for sock in [_host_video_socket_ref, _host_audio_socket_ref]:
        if sock:
            try:
                sock.close()
            except (OSError, socket.error):
                pass
