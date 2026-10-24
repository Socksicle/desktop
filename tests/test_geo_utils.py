"""Unit tests for the geo_utils HTTP response parser (no real connections)."""
import pytest

from utils.geo_utils import _read_http_response, MAX_RESPONSE_BYTES

BODY = b'{"status":"success","countryCode":"DE","query":"1.2.3.4"}'


class FakeSocket:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.timeout = None

    def recv(self, n):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def settimeout(self, timeout):
        self.timeout = timeout


def _response(body=BODY, status="200 OK", extra_headers=""):
    return (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"{extra_headers}"
        "Connection: close\r\n\r\n"
    ).encode() + body


def test_parser_reads_valid_responses():
    # 1. Full response in one chunk
    sock = FakeSocket([_response()])
    assert _read_http_response(sock, timeout=1) == BODY

    # 2. Split across chunks byte-by-byte
    raw = _response()
    sock = FakeSocket([raw[i:i + 4] for i in range(0, len(raw), 4)])
    assert _read_http_response(sock, timeout=1) == BODY

    # 3. Close-delimited body
    raw_close = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + BODY
    sock_close = FakeSocket([raw_close[i:i + 7] for i in range(0, len(raw_close), 7)])
    assert _read_http_response(sock_close, timeout=1) == BODY

    # 4. Chunked body
    raw_chunked = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + b"%x\r\n" % 10 + BODY[:10] + b"\r\n"
        + b"%x\r\n" % (len(BODY) - 10) + BODY[10:] + b"\r\n"
        + b"0\r\n\r\n"
    )
    sock_chunked = FakeSocket([raw_chunked])
    assert _read_http_response(sock_chunked, timeout=1) == BODY


def test_parser_rejects_errors_and_malformed_input():
    cases = [
        # Non-200 status
        _response(status="500 Internal Server Error", body=b"err"),
        _response(status="403 Forbidden", body=b"err"),
        # Empty body
        _response(body=b""),
        b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n",
        # Missing header terminator
        b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n",
        # Oversized
        _response(body=b"x" * (MAX_RESPONSE_BYTES + 1)),
        # Truncated chunked
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"%x\r\n" % len(BODY) + BODY,
        # Malformed chunk size
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzzz\r\n" + BODY + b"\r\n0\r\n\r\n",
    ]
    for raw in cases:
        sock = FakeSocket([raw])
        assert _read_http_response(sock, timeout=1) is None

    # Closed connection before full body
    raw_partial = _response()
    sock_partial = FakeSocket([raw_partial[:raw_partial.find(BODY) + 2]])
    assert _read_http_response(sock_partial, timeout=1) is None


def test_parser_trims_body_to_content_length():
    raw = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: " + str(len(BODY)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + BODY + b"EXTRA"
    )
    sock = FakeSocket([raw[:20], raw[20:]])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_sets_socket_timeout():
    sock = FakeSocket([_response()])
    _read_http_response(sock, timeout=7)
    assert sock.timeout == 7