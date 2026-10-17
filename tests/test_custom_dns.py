import pytest
from utils.server_model import Server, ProxyProtocol
from utils.engines.singbox_config import _generate_config as _gen_singbox
from utils.engines.xray_engine import _generate_config as _gen_xray

def test_singbox_tun_custom_dns():
    server = Server(
        protocol=ProxyProtocol.VLESS,
        host="example.com",
        port=443,
        uuid="test-uuid",
    )
    cfg = _gen_singbox(server, 1080, tun_mode=True, custom_dns="https://dns.quad9.net/dns-query")
    assert "dns" in cfg
    remote_dns = cfg["dns"]["servers"][0]
    assert remote_dns["server"] == "dns.quad9.net"
    assert remote_dns["type"] == "https"


def test_xray_custom_dns():
    server = Server(
        protocol=ProxyProtocol.VLESS,
        host="example.com",
        port=443,
        uuid="test-uuid",
    )
    cfg = _gen_xray(server, 1080, custom_dns="https://dns.adguard-dns.com/dns-query")
    assert "dns" in cfg
    assert "https://dns.adguard-dns.com/dns-query" in cfg["dns"]["servers"]


def test_xray_rejects_dot_dns():
    """Xray has no DoT support, so tls:// must fail loudly instead of being
    silently downgraded to plaintext DNS-over-TCP."""
    server = Server(
        protocol=ProxyProtocol.VLESS,
        host="example.com",
        port=443,
        uuid="test-uuid",
    )
    with pytest.raises(ValueError):
        _gen_xray(server, 1080, custom_dns="tls://1.1.1.1")
