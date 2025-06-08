# login_gui.py

import threading
import tkinter as tk
from tkinter import messagebox
import socket
import json
import ssl
import logging
import time

import host_streamer
import viewer_logic

# --- Constants & Globals ---
WINDOW_WIDTH = 600
WINDOW_HEIGHT = 550
LOGIN_SERVER_HOST = '127.0.0.1'
LOGIN_SERVER_PORT = 12350
MAX_BUFFER_SIZE = 4096

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s (login_gui): %(message)s",
    handlers=[logging.FileHandler("login_gui.log"), logging.StreamHandler()]
)


class LiveStreamApp:
    """Manages the entire Tkinter GUI application and its state."""

    def __init__(self, root_tk):
        self.root = root_tk
        self.root.title("Live Streaming App")

        # --- Application State ---
        self.is_streaming = False
        self.is_viewing = False
        self.logged_in_username = None
        self.current_stream_id = None
        self.joined_stream_id = None

        # --- Widget References ---
        self.create_stream_button = None
        self.stop_stream_button = None
        self.copy_stream_id_button = None
        self.join_stream_button = None
        self.leave_stream_button = None
        self.join_stream_entry = None
        self.status_label = None
        self.stream_id_label = None

        self._center_window()
        self.root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.show_main_screen()

    def _center_window(self):
        """Centers the main window on the screen."""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x_coord = int((screen_w / 2) - (WINDOW_WIDTH / 2))
        y_coord = int((screen_h / 2) - (WINDOW_HEIGHT / 2))
        self.root.geometry(
            f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x_coord}+{y_coord}")

    def _clear_screen(self):
        """Destroys all widgets in the root window."""
        for widget in self.root.winfo_children():
            widget.destroy()

    def _add_status_bar(self, text="Status: Initializing..."):
        """Adds a status bar to the bottom of the window."""
        status_frame = tk.Frame(self.root, bd=1, relief=tk.SUNKEN)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = tk.Label(status_frame, text=text, anchor=tk.W,
                                     padx=5)
        self.status_label.pack(fill=tk.X)

    def _update_status(self, text):
        """Updates the text in the status bar."""
        if self.status_label:
            self.status_label.config(text=f"Status: {text}")

    # --- Network Communication ---
    @staticmethod
    def send_request_to_login_server(request_payload):
        """Sends a secure, length-prefixed request to the login server."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection((LOGIN_SERVER_HOST,
                                           LOGIN_SERVER_PORT),
                                          timeout=10) as sock:
                with context.wrap_socket(sock,
                                         server_hostname=LOGIN_SERVER_HOST
                                         ) as ssl_socket:
                    payload_bytes = json.dumps(request_payload).encode('utf-8')
                    ssl_socket.sendall(
                        len(payload_bytes).to_bytes(4, 'big') + payload_bytes)

                    len_prefix = ssl_socket.recv(4)
                    if not len_prefix:
                        return "ERROR:No response from server."
                    res_len = int.from_bytes(len_prefix, 'big')
                    return ssl_socket.recv(res_len).decode('utf-8')
        except Exception as e:
            return f"ERROR:Network request failed: {e}"

    # --- Authentication Logic ---
    def login_user(self, username, password):
        """Handles the user login process."""
        self._update_status("Logging in...")

        def task():
            if not username or not password:
                messagebox.showerror("Input Required",
                                     "Username and password are required.")
                self._update_status("Login failed")
                return

            payload = {"action": "LOGIN", "username": username,
                       "password": password}
            response = self.send_request_to_login_server(payload)

            def update_ui():
                if response.startswith("SUCCESS:"):
                    self.logged_in_username = username
                    self.show_dashboard()
                else:
                    messagebox.showerror("Login Failed",
                                         response.replace("ERROR:", ""))
                    self._update_status("Login failed")

            self.root.after(0, update_ui)

        threading.Thread(target=task, daemon=True).start()

    def register_user(self, username, password, confirm):
        """Handles the user registration process."""
        self._update_status("Registering...")

        def task():
            if not username or not password:
                msg = "Username and password cannot be empty."
            elif password != confirm:
                msg = "Passwords do not match!"
            else:
                payload = {"action": "REGISTER", "username": username,
                           "password": password,
                           "confirm_password": confirm}
                response = self.send_request_to_login_server(payload)
                msg = response.replace("SUCCESS:", "").replace("ERROR:", "")

            def update_ui():
                if "successfully" in msg and not msg.startswith("ERROR"):
                    messagebox.showinfo("Success", msg)
                    self.show_login_screen()
                else:
                    messagebox.showerror("Registration Failed", msg)
                    self._update_status("Registration failed")

            self.root.after(0, update_ui)

        threading.Thread(target=task, daemon=True).start()

    def logout(self):
        """Handles the user logout process."""
        if self.is_streaming or self.is_viewing:
            messagebox.showwarning("Logout Denied",
                                   "Stop streaming or viewing first.")
            return
        self.send_request_to_login_server(
            {"action": "LOGOUT", "username": self.logged_in_username})
        self.logged_in_username = None
        self.show_main_screen()
        self._update_status("Logged out")

    # --- GUI Screens ---
    def show_main_screen(self):
        self._clear_screen()
        self._add_status_bar("Ready")
        tk.Label(self.root, text="Live Streamer",
                 font=("Arial", 26, "bold")).pack(pady=(40, 20))
        tk.Button(self.root, text="Login", font=("Arial", 16), width=15,
                  command=self.show_login_screen).pack(pady=10)
        tk.Button(self.root, text="Sign Up", font=("Arial", 16), width=15,
                  command=self.show_signup_screen).pack(pady=10)

    def show_login_screen(self):
        self._clear_screen()
        self._add_status_bar("Login")
        tk.Label(self.root, text="User Login",
                 font=("Arial", 24, "bold")).pack(pady=(30, 20))
        form = tk.Frame(self.root)
        form.pack(pady=10, padx=20)
        tk.Label(form, text="Username:", font=("Arial", 14)).grid(
            row=0, column=0, padx=5, pady=10, sticky="w")
        user_e = tk.Entry(form, font=("Arial", 14), width=30)
        user_e.grid(row=0, column=1, padx=5, pady=10)
        tk.Label(form, text="Password:", font=("Arial", 14)).grid(
            row=1, column=0, padx=5, pady=10, sticky="w")
        pass_e = tk.Entry(form, font=("Arial", 14), show='*', width=30)
        pass_e.grid(row=1, column=1, padx=5, pady=10)
        user_e.focus_set()

        btns = tk.Frame(self.root)
        btns.pack(pady=20)
        tk.Button(btns, text="Login", font=("Arial", 14, "bold"), width=10,
                  command=lambda: self.login_user(user_e.get(),
                                                  pass_e.get())).pack(
            side=tk.LEFT, padx=10)
        tk.Button(btns, text="Back", font=("Arial", 14), width=10,
                  command=self.show_main_screen).pack(side=tk.LEFT, padx=10)

    def show_signup_screen(self):
        self._clear_screen()
        self._add_status_bar("Sign Up")
        tk.Label(self.root, text="Create Account",
                 font=("Arial", 24, "bold")).pack(pady=(30, 15))
        form = tk.Frame(self.root)
        form.pack(pady=10, padx=20)
        tk.Label(form, text="Username:", font=("Arial", 14)).grid(
            row=0, column=0, padx=5, pady=8, sticky="w")
        user_e = tk.Entry(form, font=("Arial", 14), width=30)
        user_e.grid(row=0, column=1, padx=5, pady=8)
        tk.Label(form, text="Password:", font=("Arial", 14)).grid(
            row=1, column=0, padx=5, pady=8, sticky="w")
        pass_e = tk.Entry(form, font=("Arial", 14), show='*', width=30)
        pass_e.grid(row=1, column=1, padx=5, pady=8)
        tk.Label(form, text="Confirm Pwd:", font=("Arial", 14)).grid(
            row=2, column=0, padx=5, pady=8, sticky="w")
        conf_e = tk.Entry(form, font=("Arial", 14), show='*', width=30)
        conf_e.grid(row=2, column=1, padx=5, pady=8)
        user_e.focus_set()

        btns = tk.Frame(self.root)
        btns.pack(pady=15)
        tk.Button(btns, text="Sign Up", font=("Arial", 14, "bold"), width=10,
                  command=lambda: self.register_user(
                      user_e.get(), pass_e.get(), conf_e.get())
                  ).pack(side=tk.LEFT, padx=10)
        tk.Button(btns, text="Back", font=("Arial", 14), width=10,
                  command=self.show_main_screen).pack(side=tk.LEFT, padx=10)

    def show_dashboard(self):
        self._clear_screen()
        self._add_status_bar("Idle")
        tk.Label(self.root, text="Dashboard",
                 font=("Arial", 24, "bold")).pack(pady=(15, 5))
        tk.Label(self.root, text=f"Welcome, {self.logged_in_username}!",
                 font=("Arial", 14)).pack(pady=(0, 15))

        self._create_host_frame()
        self._create_viewer_frame()

        tk.Button(self.root, text="Logout", font=("Arial", 12),
                  command=self.logout, width=12).pack(pady=(20, 10))
        self._update_button_states()

    def _create_host_frame(self):
        host_f = tk.LabelFrame(self.root, text="Host Options", padx=10,
                               pady=10, font=("Arial", 12, "italic"))
        host_f.pack(pady=5, padx=20, fill="x")
        self.create_stream_button = tk.Button(
            host_f, text="Create Stream", font=("Arial", 12), width=15,
            command=self.start_host_streaming_thread)
        self.create_stream_button.grid(row=0, column=0, padx=5, pady=5)
        self.stop_stream_button = tk.Button(
            host_f, text="Stop Streaming", font=("Arial", 12), fg="#AA0000",
            width=15, command=self.stop_streaming)
        self.stop_stream_button.grid(row=0, column=1, padx=5, pady=5)
        self.stream_id_label = tk.Label(
            host_f, text="Stream ID: N/A", font=("Arial", 11))
        self.stream_id_label.grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.copy_stream_id_button = tk.Button(
            host_f, text="Copy ID", font=("Arial", 10), width=8,
            command=self.copy_stream_id_to_clipboard)
        self.copy_stream_id_button.grid(row=1, column=1, padx=5, pady=5,
                                        sticky='e')

    def _create_viewer_frame(self):
        view_f = tk.LabelFrame(self.root, text="Viewer Options", padx=10,
                               pady=10, font=("Arial", 12, "italic"))
        view_f.pack(pady=10, padx=20, fill="x")
        tk.Label(view_f, text="Stream ID:", font=("Arial", 12)).grid(
            row=0, column=0, padx=5, pady=5, sticky='w')
        self.join_stream_entry = tk.Entry(view_f, font=("Arial", 12),
                                          width=28)
        self.join_stream_entry.grid(row=0, column=1, padx=5, pady=5,
                                    sticky='ew')
        self.join_stream_button = tk.Button(
            view_f, text="Join", font=("Arial", 12, "bold"), width=8,
            command=lambda: self.start_viewer_thread(
                self.join_stream_entry.get()))
        self.join_stream_button.grid(row=0, column=2, padx=(10, 5), pady=5)
        self.leave_stream_button = tk.Button(
            view_f, text="Leave Stream", font=("Arial", 12), fg="#DD5500",
            width=15, command=self.stop_viewing)
        self.leave_stream_button.grid(row=1, column=1, columnspan=2,
                                      pady=(8, 5))
        view_f.columnconfigure(1, weight=1)

    # --- Streaming and Viewing Logic ---
    def start_host_streaming_thread(self):
        if self.is_streaming or self.is_viewing:
            messagebox.showwarning("Action Denied",
                                   "Cannot host while already active.")
            return
        self._update_status("Starting stream...")
        self.is_streaming = True
        self._update_button_states()

        def task():
            success, msg, r_id = host_streamer.launch_host_threads(
                master_tk_root=self.root,
                on_stream_end_callback=self.handle_host_initiated_exit)

            def update_ui():
                if success and r_id:
                    self.current_stream_id = r_id
                    self._update_status(f"Streaming as "
                                        f"{self.logged_in_username}")
                    self.stream_id_label.config(
                        text=f"Stream ID: {self.current_stream_id}")
                    messagebox.showinfo(
                        "Success",
                        f"Streaming started! ID: {self.current_stream_id}")
                else:
                    if self.is_streaming:
                        self.handle_host_initiated_exit()
                    messagebox.showerror("Error", f"Failed to stream:\n{msg}")
                self._update_button_states()

            self.root.after(0, update_ui)

        threading.Thread(target=task, daemon=True).start()

    def stop_streaming(self):
        if self.is_streaming:
            host_streamer.stop_host_streaming()

    def handle_host_initiated_exit(self):
        def update_ui():
            if not self.is_streaming:
                return
            old_id = self.current_stream_id
            self.is_streaming = False
            self.current_stream_id = None
            self._update_status("Idle")
            self.stream_id_label.config(text="Stream ID: N/A")
            self._update_button_states()
            messagebox.showinfo("Stream Ended",
                                f"You have stopped hosting stream {old_id}.")
            if host_streamer.streaming_active.is_set():
                host_streamer.stop_host_streaming()

        self.root.after(0, update_ui)

    def start_viewer_thread(self, stream_id):
        if self.is_viewing or self.is_streaming:
            messagebox.showwarning("Action Denied",
                                   "Cannot view while already active.")
            return
        if not stream_id.strip():
            messagebox.showerror("Error", "Stream ID cannot be empty.")
            return

        self._update_status(f"Joining {stream_id}...")
        self.is_viewing = True
        self.joined_stream_id = stream_id.strip()
        self._update_button_states()

        def task():
            success, msg = viewer_logic.launch_viewer_threads(
                self.joined_stream_id, master_tk_root=self.root,
                on_exit_callback=self.handle_viewer_exit)

            def update_ui():
                if success:
                    if self.is_viewing:
                        self._update_status(f"Viewing {self.joined_stream_id}")
                    messagebox.showinfo("Success", f"Joined stream!")
                else:
                    if self.is_viewing:
                        self.handle_viewer_exit(f"Failed to join:\n{msg}")
                self._update_button_states()

            self.root.after(0, update_ui)

        threading.Thread(target=task, daemon=True).start()

    def stop_viewing(self):
        if self.is_viewing:
            viewer_logic.stop_viewer_streaming()

    def handle_viewer_exit(self, exit_message):
        def update_ui():
            if not self.is_viewing:
                return
            self.is_viewing = False
            self.joined_stream_id = None
            self._update_status("Idle")
            self._update_button_states()
            messagebox.showinfo("Stream Closed", exit_message)
            if viewer_logic.viewer_active.is_set():
                viewer_logic.stop_viewer_streaming()

        self.root.after(0, update_ui)

    # --- Utility and Cleanup ---
    def copy_stream_id_to_clipboard(self):
        if self.current_stream_id and self.is_streaming:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.current_stream_id)
            messagebox.showinfo("Copied",
                                f"ID '{self.current_stream_id}' copied.")
        else:
            messagebox.showwarning("No Stream ID", "Not hosting a stream.")

    def _update_button_states(self):
        """Enables/disables buttons based on the application state."""
        is_idle = not self.is_streaming and not self.is_viewing
        if self.create_stream_button:
            self.create_stream_button.config(
                state=tk.NORMAL if is_idle else tk.DISABLED)
            self.stop_stream_button.config(
                state=tk.NORMAL if self.is_streaming else tk.DISABLED)
            self.copy_stream_id_button.config(
                state=tk.NORMAL if self.is_streaming else tk.DISABLED)
            self.join_stream_button.config(
                state=tk.NORMAL if is_idle else tk.DISABLED)
            self.join_stream_entry.config(
                state=tk.NORMAL if is_idle else tk.DISABLED)
            self.leave_stream_button.config(
                state=tk.NORMAL if self.is_viewing else tk.DISABLED)

    def on_closing(self):
        """Handles the application window being closed."""
        if self.is_streaming:
            if not messagebox.askokcancel("Quit",
                                          "Stop streaming and quit?"):
                return
        elif self.is_viewing:
            if not messagebox.askokcancel("Quit", "Leave stream and quit?"):
                return

        def cleanup_and_quit():
            if self.is_streaming:
                host_streamer.stop_host_streaming()
            if self.is_viewing:
                viewer_logic.stop_viewer_streaming()
            if self.logged_in_username:
                self.send_request_to_login_server(
                    {"action": "LOGOUT", "username": self.logged_in_username})
            time.sleep(0.5)
            self.root.after(0, self.root.destroy)

        threading.Thread(target=cleanup_and_quit, daemon=True).start()


if __name__ == "__main__":
    if not host_streamer or not viewer_logic:
        messagebox.showerror("Fatal Error",
                             "Core modules (host/viewer) not found.")
    else:
        main_root = tk.Tk()
        app = LiveStreamApp(main_root)
        main_root.mainloop()