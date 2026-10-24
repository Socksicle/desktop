import json
from unittest import mock

import pytest

from utils.server_manager import ServerManager
from utils.server_model import ProxyProtocol, Server


def test_save_manual_servers_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    mgr = ServerManager()

    server = Server(
        name="Test Node",
        host="1.2.3.4",
        port=443,
        protocol=ProxyProtocol.VLESS,
        uuid="00000000-0000-0000-0000-000000000000",
    )
    mgr.manual_servers.append(server)
    mgr.save_manual_servers()

    # Verify file was written and can be reloaded
    assert (tmp_path / "servers.json").exists()
    mgr2 = ServerManager()
    assert len(mgr2.manual_servers) == 1
    assert mgr2.manual_servers[0].name == "Test Node"


def test_save_manual_servers_failure_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    mgr = ServerManager()

    server = Server(
        name="Original Node",
        host="1.2.3.4",
        port=443,
        protocol=ProxyProtocol.VLESS,
        uuid="00000000-0000-0000-0000-000000000000",
    )
    mgr.manual_servers.append(server)
    mgr.save_manual_servers()

    original_content = (tmp_path / "servers.json").read_text(encoding="utf-8")

    # Add another server but fail during json.dump
    mgr.manual_servers.append(Server(
        name="New Node",
        host="5.6.7.8",
        port=8443,
        protocol=ProxyProtocol.VLESS,
        uuid="11111111-1111-1111-1111-111111111111",
    ))

    def fail_dump(*args, **kwargs):
        raise OSError("Disk full simulation")

    monkeypatch.setattr("json.dump", fail_dump)
    mgr.save_manual_servers()

    # Original servers.json must remain completely intact and not truncated
    assert (tmp_path / "servers.json").read_text(encoding="utf-8") == original_content

    # No leftover .tmp files
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0
