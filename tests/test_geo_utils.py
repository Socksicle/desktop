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


def test_parser_reads_full_response_in_one_chunk():
    sock = FakeSocket([_response()])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_reads_split_headers_and_body():
    raw = _response()
    middle = raw.find(b"\r\n\r\n") + 4
    sock = FakeSocket([raw[:middle], raw[middle:]])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_reads_body_byte_by_byte():
    raw = _response()
    sock = FakeSocket([raw[i:i + 1] for i in range(len(raw))])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_rejects_non_200_status():
    raw = _response(status="500 Internal Server Error", body=b"nope")
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) is None


def test_parser_rejects_empty_body():
    raw = _response(body=b"")
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) is None


def test_parser_rejects_missing_header_terminator():
    sock = FakeSocket([b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n"])
    assert _read_http_response(sock, timeout=1) is None


def test_parser_trims_body_to_content_length():
    raw = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: " + str(len(BODY)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + BODY + b"EXTRA"
    )
    sock = FakeSocket([raw[:20], raw[20:]])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_rejects_oversized_content_length():
    raw = _response(body=b"x" * (MAX_RESPONSE_BYTES + 1))
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) is None


def test_parser_rejects_closed_connection_before_full_body():
    raw = _response()
    middle = raw.find(BODY) + 2
    sock = FakeSocket([raw[:middle]])
    assert _read_http_response(sock, timeout=1) is None


def test_parser_sets_socket_timeout():
    sock = FakeSocket([_response()])
    _read_http_response(sock, timeout=7)
    assert sock.timeout == 7


def test_parser_reads_close_delimited_body():
    raw = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + BODY
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_reads_close_delimited_body_across_chunks():
    raw = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + BODY
    sock = FakeSocket([raw[i:i + 7] for i in range(0, len(raw), 7)])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_reads_chunked_body():
    raw = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + b"%x\r\n" % 10 + BODY[:10] + b"\r\n"
        + b"%x\r\n" % (len(BODY) - 10) + BODY[10:] + b"\r\n"
        + b"0\r\n\r\n"
    )
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_reads_chunked_body_with_split_size_line():
    raw = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + b"%x\r\n" % len(BODY) + BODY + b"\r\n0\r\n\r\n"
    )
    marker = raw.find(b"\r\n\r\n") + 4 + 3
    sock = FakeSocket([raw[:marker], raw[marker:]])
    assert _read_http_response(sock, timeout=1) == BODY


def test_parser_rejects_truncated_chunked_body():
    raw = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + b"%x\r\n" % len(BODY) + BODY
    )
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) is None


def test_parser_rejects_malformed_chunk_size():
    raw = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"zzz\r\n" + BODY + b"\r\n0\r\n\r\n"
    )
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) is None


def test_parser_rejects_status_403():
    raw = _response(status="403 Forbidden", body=b'{"status":"fail"}')
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) is None


def test_parser_rejects_empty_close_delimited_body():
    raw = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n"
    sock = FakeSocket([raw])
    assert _read_http_response(sock, timeout=1) is None