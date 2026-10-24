"""Engine manager: registry and selection.

Provides a central place to:
  - List available engines
  - Get the currently selected engine
  - Check whether an engine binary is available and usable
  - Switch engines with proper cleanup
"""
import logging

from ..i18n import tr
from .base import ProxyEngine, EngineType, CheckResult

log = logging.getLogger("engine.manager")

# Singleton registry
_ENGINES: dict[EngineType, type[ProxyEngine]] = {}
_INSTANCES: dict[EngineType, ProxyEngine] = {}


def register_engine(cls: type[ProxyEngine]):
    """Register an engine class. Call at import time."""
    _ENGINES[cls.engine_type] = cls
    return cls


def get_engine_classes() -> dict[EngineType, type[ProxyEngine]]:
    """Return all registered engine classes."""
    return dict(_ENGINES)


def get_all_engine_types() -> list[EngineType]:
    """Return all registered engine types in display order."""
    return list(_ENGINES.keys())


def get_engine(cls_or_type) -> ProxyEngine:
    """Get or create a singleton engine instance.

    Unregistered or invalid engine types fall back to sing-box.
    """
    if isinstance(cls_or_type, type):
        et = cls_or_type.engine_type
        cls = cls_or_type
    else:
        et = cls_or_type
        cls = _ENGINES.get(et)
    if cls is None:
        log.warning("Engine %r is not available, falling back to sing-box", et)
        et = EngineType.SINGBOX
        cls = _ENGINES[et]
    if et not in _INSTANCES:
        _INSTANCES[et] = cls()
    return _INSTANCES[et]


def resolve_engine_for_server(server, settings: dict) -> EngineType:
    """Resolve the appropriate EngineType for a given server and settings.

    In 'auto' mode (or when server capabilities strictly dictate), picks:
      - Protocols exclusive to sing-box (hysteria2, tuic, trojan) -> sing-box
      - Transports exclusive to Xray (xhttp, splithttp, kcp) -> xray
      - When TUN mode is enabled -> sing-box (native Wintun/sing-tun) —
        this wins over any explicit engine preference, because only
        sing-box provides TUN; xray would silently run without it
      - Otherwise checks engine binary availability, preferring sing-box.
    """
    engine_key = settings.get("engine", "auto")
    proto = getattr(getattr(server, "protocol", None), "value", getattr(server, "protocol", ""))
    proto_val = str(proto).lower().strip() if proto else ""
    transport = getattr(server, "transport", "tcp") or "tcp"
    transport_val = str(transport).lower().strip() if transport else ""

    if settings.get("tun_mode", False):
        return EngineType.SINGBOX

    if proto_val in ("hysteria2", "tuic", "trojan"):
        return EngineType.SINGBOX

    if transport_val in ("xhttp", "splithttp", "kcp"):
        return EngineType.XRAY

    if engine_key in ("sing-box", "xray"):
        return EngineType(engine_key)

    if check_engine(EngineType.SINGBOX).usable:
        return EngineType.SINGBOX

    if check_engine(EngineType.XRAY).usable:
        return EngineType.XRAY

    return EngineType.SINGBOX


def get_current_engine(settings: dict) -> ProxyEngine:
    """Get the engine instance for the currently selected engine type."""
    engine_key = settings.get("engine", "auto")
    if engine_key == "auto":
        et = EngineType.SINGBOX
    else:
        try:
            et = EngineType(engine_key)
            if et not in _ENGINES:
                et = EngineType.SINGBOX
        except ValueError:
            et = EngineType.SINGBOX
    return get_engine(et)



def check_engine(engine_type: EngineType) -> CheckResult:
    """Check if a specific engine binary is available and usable."""
    engine = get_engine(engine_type)
    binary = engine.find_binary()
    if binary is None:
        return CheckResult(False, tr("engine.binary_missing").format(engine=engine_type.value))
    return engine.check_usable(binary)


def switch_engine(settings: dict, new_engine_type: EngineType,
                  connection_manager=None) -> ProxyEngine:
    """Switch to a new engine. Disconnects the current one if connected.

    Returns the new engine instance.
    """
    if connection_manager and connection_manager.is_connected:
        connection_manager.disconnect()

    engine = get_engine(new_engine_type)
    settings["engine"] = engine.engine_type.value
    return engine


def cleanup_stale_core_processes() -> list[str]:
    """Kill engine processes left alive by a previously crashed session.

    Only processes recorded in our own pid markers are touched; foreign
    processes and recycled pids are never killed.  Returns a list of
    actions performed (empty when there was nothing to clean).
    """
    from .proc_guard import cleanup_stale_engines
    return cleanup_stale_engines([et.value for et in _ENGINES] + ["xray-bridge"])


# Auto-register all engines on import
def _register_all():
    from .xray_engine import XrayEngine
    from .singbox_engine import SingBoxEngine
    register_engine(SingBoxEngine)
    register_engine(XrayEngine)


_register_all()
