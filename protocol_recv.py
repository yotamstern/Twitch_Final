# protocol_recv.py
import struct
import socket


def receive_data(client_socket):
    """
    Receives length-prefixed data from a socket.

    The protocol expects a 4-byte unsigned integer (big-endian)
    representing the payload size, followed by the payload itself.

    Args:
        client_socket (socket.socket): The socket to receive from.

    Returns:
        A tuple (payload_size, payload_data) if successful.
        Returns (0, None) if the connection is closed cleanly or on error.
    """
    try:
        # Receive the 4-byte size prefix
        size_prefix = client_socket.recv(4)
        if not size_prefix:
            return 0, None

        # Unpack the size
        payload_size = struct.unpack("I", size_prefix)[0]

        # Receive the full payload
        data = b''
        while len(data) < payload_size:
            remaining_bytes = payload_size - len(data)
            chunk = client_socket.recv(remaining_bytes)
            if not chunk:
                # Connection lost before full payload was received
                return 0, None
            data += chunk

        return payload_size, data

    except (socket.error, struct.error, ConnectionResetError):
        # Treat these network-related errors as a graceful disconnect
        return 0, None