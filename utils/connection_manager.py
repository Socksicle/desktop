"""High-level connection orchestration using the engine abstraction.

Connection flow:

    DISCONNECTED -> CONNECTING -> [engine running?] -> [local SOCKS5 proxy
    responds to a handshake?] -> CONNECTED

On any failure the manager tears the engine down and returns to DISCONNECTED,
reporting the error through `statusChanged`. Connected-ness is verified by
actually probing the local proxy, never by waiting a fixed delay and
assuming success.
"""
import logging
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot, QThreadPool, QRunnable

from .geo_utils import fetch_ip_info_via_proxy
from .i18n import tr
from .ping import socks5_proxy_ready, ProxyPingJob, parse_ping_url, DEFAULT_PING_TIMEOUT
from .engines.engine_manager import get_current_engine
from .engines.base import DEFAULT_LOCAL_PORT, check_local_port
from .engines.singbox_config import (
    MUX_DEFAULT_PROTOCOL,
    MUX_MAX_CONNECTIONS_DEFAULT,
)

log = logging.getLogger("connection_manager")

PROBE_INTERVAL_MS = 250
PROBE_TIMEOUT_S = 25.0

GEO_RETRY_ATTEMPTS = 3
GEO_RETRY_PAUSE_S = 1.5
GEO_RETRY_INTERVAL_S = 10.0

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"


class _AsyncProbeJob(QRunnable):
    """Probe SOCKS5 proxy readiness in a worker thread without blocking the GUI event loop."""

    def __init__(self, manager, gen: int, port: int):
        super().__init__()
        self.manager = manager
        self.gen = gen
        self.port = port

    def run(self):
        ready = False
        try:
            ready = socks5_proxy_ready(self.port, timeout=0.25)
        except (OSError, ValueError, TypeError):
            ready = False
        self.manager.asyncProbeFinished.emit(self.gen, ready)


class ConnectionManager(QObject):
    statusChanged = Signal(str, bool)      # message, is_error
    connectionStateChanged = Signal(bool)  # connected
    logUpdated = Signal(str)               # engine output line
    geoInfoReady = Signal(dict)            # {ip, flag} after connect
    geoError = Signal(str)                 # reason when geo lookup failed
    pingResultReady = Signal(object)       # active ping in ms (None on error)
    asyncProbeFinished = Signal(int, bool)  # (generation, ready) from probe thread
    startProbeRequested = Signal()         # cross-thread (re)start of probing
    stopTimersRequested = Signal()         # cross-thread probe/ping timer stop
    killSwitchFailed = Signal(str)         # reason; connection must be stopped

    def __init__(self, settings=None):
        super().__init__()
        self._settings = settings or {}
        self.state = DISCONNECTED
        self.is_connecting = False
        self.current_geo = None
        self.kill_switch_enabled = False
        self._generation = 0
        self._geo_last_attempt = 0.0
        self._probing_in_flight = False
        self._last_connected_server = None
        self._auto_reconnect_attempts = 0
        self.MAX_AUTO_RECONNECTS = 3
        # Single automatic retry for a stale wintun adapter (see engine hint):
        # the driver usually releases the name within seconds, so one delayed
        # retry succeeds where the first start raced it.
        self._tun_retries = 1
        # Serializes toggle()/disconnect() from any thread: rapid UI clicks
        # queue up instead of racing for the engine subprocess / local port.
        self._toggle_lock = threading.RLock()

        self._probe_deadline = 0.0
        self.probe_timer = QTimer(self)
        self.probe_timer.setInterval(PROBE_INTERVAL_MS)
        self.probe_timer.timeout.connect(self._probe)

        self.ping_timer = QTimer(self)
        self.ping_timer.setInterval(60000)  # Ping once per minute
        self.ping_timer.timeout.connect(self._update_ping)

        self._engine = get_current_engine(self._settings)
        self._engine.statusChanged.connect(self._on_status_changed)
        self._engine.connectionStateChanged.connect(self._on_connection_state_changed)
        self._engine.logUpdated.connect(self.logUpdated)
        # Auto-connection delivers across threads (queued) and directly
        # within the GUI thread — replaces the QMetaObject.invokeMethod calls.
        self.asyncProbeFinished.connect(self._on_async_probe_result)
        self.startProbeRequested.connect(self._start_probe)
        self.stopTimersRequested.connect(self._stop_timers)
        self.apply_settings()

    @property
    def engine(self):
        return self._engine

    @property
    def is_connected(self):
        return self.state == CONNECTED

    @property
    def is_reconnecting(self):
        return self._auto_reconnect_attempts > 0 and self.state != CONNECTED

    @property
    def local_port(self):
        return self._engine.local_port

    @local_port.setter
    def local_port(self, port):
        try:
            self._engine.local_port = check_local_port(port)
        except (TypeError, ValueError, OverflowError):
            log.warning("Invalid local port %r in settings; falling back to %s",
                        port, DEFAULT_LOCAL_PORT)
            self._engine.local_port = DEFAULT_LOCAL_PORT

    def apply_settings(self, settings=None):
        """Read settings and apply them to the engine."""
        if settings is not None:
            self._settings.update(settings)
        self.local_port = self._settings.get("local_port", DEFAULT_LOCAL_PORT)
        tun_mode = self._settings.get("tun_mode", False)
        custom_dns = self._settings.get("custom_dns", None)
        if hasattr(self._engine, "custom_dns"):
            self._engine.custom_dns = custom_dns
        if hasattr(self._engine, "hy2_insecure"):
            self._engine.hy2_insecure = bool(self._settings.get("hy2_insecure", False))
        if hasattr(self._engine, "block_udp"):
            self._engine.block_udp = bool(self._settings.get("block_udp", False))
        if hasattr(self._engine, "bypass_lan"):
            self._engine.bypass_lan = bool(self._settings.get("bypass_lan", True))
        if hasattr(self._engine, "tls_fragment"):
            self._engine.tls_fragment = bool(self._settings.get("tls_fragment", False))
        if hasattr(self._engine, "mux_enabled"):
            self._engine.mux_enabled = bool(self._settings.get("mux_enabled", False))
            self._engine.mux_protocol = self._settings.get(
                "mux_protocol", MUX_DEFAULT_PROTOCOL)
            self._engine.mux_max_connections = self._settings.get(
                "mux_max_connections", MUX_MAX_CONNECTIONS_DEFAULT)
        self.kill_switch_enabled = bool(self._settings.get("killswitch", False))
        engine_val = self._settings.get("engine", "auto")
        if tun_mode:
            from .engines.base import EngineType
            from .engines.engine_manager import get_engine
            if getattr(self._engine, "engine_type", None) != EngineType.SINGBOX:
                self.switch_engine(get_engine(EngineType.SINGBOX))
            if hasattr(self._engine, "tun_mode"):
                self._engine.tun_mode = True
        else:
            if not self.is_connected and engine_val in ("xray", "sing-box"):
                from .engines.base import EngineType
                from .engines.engine_manager import get_engine
                target_et = EngineType(engine_val)
                if getattr(self._engine, "engine_type", None) != target_et:
                    self.switch_engine(get_engine(target_et))
            if hasattr(self._engine, "tun_mode"):
                self._engine.tun_mode = False

    @property
    def current_server(self):
        return self._engine.get_current_server()

    def switch_engine(self, engine):
        """Switch to a different engine instance."""
        if self.is_connected or self.is_connecting:
            self.disconnect()
        old_port = getattr(self._engine, "local_port", DEFAULT_LOCAL_PORT)
        old = self._engine
        try:
            old.statusChanged.disconnect(self._on_status_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            old.connectionStateChanged.disconnect(self._on_connection_state_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            old.logUpdated.disconnect(self.logUpdated)
        except (RuntimeError, TypeError):
            pass
        self._engine = engine
        engine.statusChanged.connect(self._on_status_changed)
        engine.connectionStateChanged.connect(self._on_connection_state_changed)
        engine.logUpdated.connect(self.logUpdated)
        engine.local_port = old_port
        # apply_settings() and _toggle_locked() set engine-level options on
        # the active engine before (or independently of) a switch; without
        # this carry-over a dropped engine takes custom_dns and the toggles
        # with it and the replacement silently falls back to defaults.
        for attr in ("custom_dns", "hy2_insecure", "block_udp", "tun_mode",
                     "bypass_lan", "tls_fragment", "mux_enabled", "mux_protocol",
                     "mux_max_connections"):
            if hasattr(old, attr) and hasattr(engine, attr):
                setattr(engine, attr, getattr(old, attr))
        if hasattr(self._engine, "set_available_servers") and hasattr(self, "_available_servers"):
            self._engine.set_available_servers(self._available_servers)

    def set_available_servers(self, servers: list):
        """Register the list of available servers with the manager and engine."""
        self._available_servers = list(servers) if servers else []
        if hasattr(self._engine, "set_available_servers"):
            self._engine.set_available_servers(self._available_servers)

    def toggle(self, server, connect=None):
        """Start or stop the proxy. Returns True when a connect was initiated.

        The whole operation runs under _toggle_lock, so overlapping calls
        from rapid UI clicks serialize instead of fighting over the engine
        subprocess. A superseded in-flight connect is dropped via
        _generation once its successor takes over.
        """
        with self._toggle_lock:
            return self._toggle_locked(server, connect)

    def _toggle_locked(self, server, connect=None):
        if connect is None:
            if self.state == CONNECTING:
                self.disconnect()
                return False
            connect = not self.is_connected

        if connect:
            if server is None:
                return False

            # Hot-switch: if connected and engine can switch servers dynamically without dropping tunnel
            if (
                self.state == CONNECTED
                and hasattr(self._engine, "can_switch_server")
                and self._engine.can_switch_server(server)
            ):
                log.info("Hot-switching server to %s without restarting tunnel", getattr(server, "name", server))
                if self._engine.switch_server(server):
                    self._last_connected_server = server
                    self.statusChanged.emit("Started", False)
                    if getattr(self, "kill_switch_enabled", False) and getattr(server, "host", ""):
                        gen = self._generation
                        host = server.host
                        port = getattr(server, "port", 443)
                        threading.Thread(target=self._enable_killswitch_async,
                                         args=(gen, host, port),
                                         daemon=True, name="KillSwitchEnable").start()
                    self.current_geo = None
                    self._geo_last_attempt = time.monotonic()
                    self._update_ping()
                    threading.Thread(target=self._fetch_geo, daemon=True).start()
                    return True
                log.info("Hot-switch failed, falling back to full reconnect")

            self._last_connected_server = server

            # Hot-reconnect: if already connecting or connected, gracefully disconnect previous instance first
            if self.state in (CONNECTING, CONNECTED) or self._engine.is_running():
                self.disconnect()
                self._last_connected_server = server

            from .engines.engine_manager import get_engine, resolve_engine_for_server
            target_engine_type = resolve_engine_for_server(server, self._settings)
            current_engine_type = getattr(self._engine, "engine_type", None)
            if current_engine_type is not None and current_engine_type != target_engine_type:
                log.info("Auto-switching engine to %s for server %s", target_engine_type.value, getattr(server, "name", server))
                self.switch_engine(get_engine(target_engine_type))

            tun_mode = self._settings.get("tun_mode", False)
            if hasattr(self._engine, "tun_mode"):
                self._engine.tun_mode = bool(tun_mode)
            if hasattr(self._engine, "hy2_insecure"):
                self._engine.hy2_insecure = bool(self._settings.get("hy2_insecure", False))
            if hasattr(self._engine, "block_udp"):
                self._engine.block_udp = bool(self._settings.get("block_udp", False))
            if hasattr(self._engine, "bypass_lan"):
                self._engine.bypass_lan = bool(self._settings.get("bypass_lan", True))
            if hasattr(self._engine, "tls_fragment"):
                self._engine.tls_fragment = bool(self._settings.get("tls_fragment", False))
            if hasattr(self._engine, "mux_enabled"):
                self._engine.mux_enabled = bool(self._settings.get("mux_enabled", False))
                self._engine.mux_protocol = self._settings.get(
                    "mux_protocol", MUX_DEFAULT_PROTOCOL)
                self._engine.mux_max_connections = self._settings.get(
                    "mux_max_connections", MUX_MAX_CONNECTIONS_DEFAULT)

            self.state = CONNECTING
            self.is_connecting = True
            self.current_geo = None
            self._tun_retries = 1
            self._generation += 1
            curr_gen = self._generation

            engine = self._engine
            if engine.start(server):
                # Verify that no disconnect/new connect happened while start() was executing
                if self.state == CONNECTING and self._generation == curr_gen:
                    # Re-arm the connecting UI: a hot-reconnect emits
                    # "Disconnected" first, which would otherwise leave the
                    # button stuck in the idle state during the handshake.
                    self.statusChanged.emit(tr("status.connecting"), False)
                    self.startProbeRequested.emit()
                    return True
                else:
                    # switch_engine() may have replaced self._engine while
                    # start() was running; stop the instance that actually
                    # started, not the replacement.
                    engine.teardown()
                    return False
            self.state = DISCONNECTED
            self.is_connecting = False
        else:
            self._last_connected_server = None
            self._auto_reconnect_attempts = 0
            self.disconnect()
        return False

    @Slot()
    def _start_probe(self):
        self._probing_in_flight = False
        self._probe_deadline = time.monotonic() + PROBE_TIMEOUT_S
        self.probe_timer.start()

    @Slot()
    def _stop_timers(self):
        self.probe_timer.stop()
        self.ping_timer.stop()

    def _disable_killswitch(self):
        try:
            from .killswitch import KillSwitchManager
            KillSwitchManager.get_instance().disable()
        except Exception as e:
            log.debug("Kill switch disable failed: %s", e)

    def disconnect(self):
        """Disconnect at any stage: failed start, crash, or mid-connect."""
        self._probing_in_flight = False
        self._disable_killswitch()
        self.stopTimersRequested.emit()
        self.state = DISCONNECTED
        self.is_connecting = False
        self.current_geo = None
        self._generation += 1
        self._engine.disconnect_from_server()

    def _probe(self):
        if not self._engine.is_running():
            self._handle_process_stopped()
            return
        if time.monotonic() > self._probe_deadline:
            self._fail(tr("conn.proxy_not_responding"))
            return
        if self._probing_in_flight:
            return
        self._probing_in_flight = True
        QThreadPool.globalInstance().start(
            _AsyncProbeJob(self, self._generation, int(self.local_port))
        )

    @Slot(int, bool)
    def _on_async_probe_result(self, gen: int, ready: bool):
        self._probing_in_flight = False
        if gen != self._generation or self.state != CONNECTING:
            return
        if not self._engine.is_running():
            self._handle_process_stopped()
            return
        if ready:
            self._on_proxy_ready()
        elif time.monotonic() > self._probe_deadline:
            self._fail(tr("conn.proxy_not_responding"))

    def _on_proxy_ready(self):
        self._probing_in_flight = False
        self._auto_reconnect_attempts = 0
        self.probe_timer.stop()
        self.state = CONNECTED
        self.is_connecting = False
        self._engine.confirm_connected()
        self.statusChanged.emit("Started", False)
        if getattr(self, "kill_switch_enabled", False) and self.current_server:
            # netsh/nft firewall setup (plus DNS resolve) blocks for hundreds
            # of milliseconds - keep it off the GUI thread so the hero
            # animation never stalls at the connect moment.
            gen = self._generation
            host = self.current_server.host
            port = self.current_server.port
            threading.Thread(target=self._enable_killswitch_async,
                             args=(gen, host, port),
                             daemon=True, name="KillSwitchEnable").start()
        self.ping_timer.start()
        self._geo_last_attempt = time.monotonic()
        self._update_ping()
        threading.Thread(target=self._fetch_geo, daemon=True).start()

    def _enable_killswitch_async(self, gen: int, host, port):
        if self.state != CONNECTED or gen != self._generation:
            return
        manager = None
        was_active = False
        try:
            from .killswitch import KillSwitchManager
            manager = KillSwitchManager.get_instance()
            was_active = bool(getattr(manager, "is_active", False))
            bin_path = getattr(self._engine, "binary_path", None) or self._engine.find_binary()
            enabled = manager.enable(host, port, bin_path)
        except Exception as e:
            log.exception("Kill Switch setup failed: %s", e)
            enabled = False

        if not enabled:
            reason = getattr(manager, "last_error", "") if manager is not None else ""
            if self.state == CONNECTED and gen == self._generation:
                self.killSwitchFailed.emit(reason or tr("killswitch.reason_unknown"))
            return

        # The user may have disconnected while the firewall was being
        # configured: undo only rules this call installed. If protection was
        # already active for the previous session, disconnect() owns cleanup.
        if not was_active and (self.state != CONNECTED or gen != self._generation):
            try:
                manager.disable()
            except Exception as e:
                log.warning("Kill Switch rollback failed: %s", e)

    def _fail(self, msg):
        self._probing_in_flight = False
        self._disable_killswitch()
        self.probe_timer.stop()
        self.ping_timer.stop()
        self.state = DISCONNECTED
        self.is_connecting = False
        self._engine.teardown()
        self.statusChanged.emit(msg, True)

    def _handle_process_stopped(self):
        self._probing_in_flight = False
        if self.state == CONNECTING:
            self._disable_killswitch()
            self.probe_timer.stop()
            self._engine.teardown()
            self.state = DISCONNECTED
            self.is_connecting = False
            code = self._engine.last_exit_code
            if code is not None:
                msg_fn = getattr(self._engine, 'exit_message', None)
                if msg_fn:
                    self.statusChanged.emit(msg_fn(code), True)
                else:
                    self.statusChanged.emit(
                        tr("conn.engine_exit").format(code=code), True)
            else:
                self.statusChanged.emit(tr("status.error"), True)
        elif self.state == CONNECTED:
            self.ping_timer.stop()
            last_server = self._last_connected_server
            self._engine.teardown()
            self.state = DISCONNECTED
            if last_server and self._auto_reconnect_attempts < self.MAX_AUTO_RECONNECTS:
                self._auto_reconnect_attempts += 1
                self.is_connecting = True
                log.info("Engine exited unexpectedly while connected. Auto-reconnecting immediately (attempt %d/%d)...",
                         self._auto_reconnect_attempts, self.MAX_AUTO_RECONNECTS)
                self.statusChanged.emit(tr("conn.reconnecting").format(
                    n=self._auto_reconnect_attempts, max=self.MAX_AUTO_RECONNECTS), False)
                QTimer.singleShot(150, lambda s=last_server: self.toggle(s, True))
            else:
                self._disable_killswitch()
                self.is_connecting = False
                self._last_connected_server = None
                self._auto_reconnect_attempts = 0
                self.statusChanged.emit(tr("conn.lost"), True)

    def _on_connection_state_changed(self, conn):
        if not conn and self.state in (CONNECTING, CONNECTED):
            self._handle_process_stopped()

    def _on_status_changed(self, msg, err):
        if err and self.state in (CONNECTING, CONNECTED):
            if self.state == CONNECTED and self._last_connected_server and self._auto_reconnect_attempts < self.MAX_AUTO_RECONNECTS:
                pass
            else:
                self._disable_killswitch()
                self.probe_timer.stop()
                self.ping_timer.stop()
                self.state = DISCONNECTED
                self.is_connecting = False
                self._engine.teardown()
                self._maybe_retry_tun_adapter()
        self.statusChanged.emit(msg, err)

    def _maybe_retry_tun_adapter(self):
        """Schedule one delayed retry after a stale-TUN-adapter failure."""
        pop = getattr(self._engine, "pop_tun_adapter_conflict", None)
        try:
            conflict = bool(pop()) if callable(pop) else False
        except (AttributeError, TypeError, ValueError, RuntimeError):
            conflict = False
        if not conflict or self._tun_retries <= 0:
            return
        server = self._last_connected_server
        if server is None:
            return
        self._tun_retries -= 1
        gen = self._generation
        log.info("Stale TUN adapter detected, retrying connect in 3s...")
        self.statusChanged.emit(tr("conn.tun_retry"), False)
        QTimer.singleShot(3000, lambda: self._retry_tun_connect(server, gen))

    def _retry_tun_connect(self, server, gen: int):
        # Only retry when nothing else happened meanwhile (user action or a
        # newer attempt must always win over the scheduled retry).
        if self.state != DISCONNECTED or gen != self._generation:
            return
        if self._last_connected_server is not server:
            return
        self.toggle(server, True)

    def _fetch_geo(self):
        gen = self._generation
        for _ in range(GEO_RETRY_ATTEMPTS):
            if self.state != CONNECTED or gen != self._generation:
                return
            info = fetch_ip_info_via_proxy(self.local_port)
            if info:
                if self.state == CONNECTED and gen == self._generation:
                    self.current_geo = info
                    try:
                        self.geoInfoReady.emit(info)
                    except (RuntimeError, ReferenceError):
                        return
                return
            for _ in range(int(GEO_RETRY_PAUSE_S * 10)):
                if self.state != CONNECTED or gen != self._generation:
                    return
                time.sleep(0.1)
        if self.state == CONNECTED and gen == self._generation:
            try:
                self.geoError.emit("geo unavailable")
            except (RuntimeError, ReferenceError):
                return

    def _update_ping(self):
        if not self.is_connected:
            return
        gen = self._generation
        timeout = float(self._settings.get("ping_timeout", DEFAULT_PING_TIMEOUT))
        url = self._settings.get("ping_url") or None
        try:
            host, _, _, _ = parse_ping_url(url)
        except ValueError as e:
            log.warning("Invalid ping URL, active ping skipped: %s", e)
            self._on_ping_result(gen, None)
            return
        QThreadPool.globalInstance().start(ProxyPingJob(
            host, int(self.local_port),
            lambda ms, gen=gen: self._on_ping_result(gen, ms),
            method="http_head",
            timeout=timeout,
            url=url))

    def _on_ping_result(self, gen, ms):
        """Forward a ping result, dropping results from older connections."""
        if gen != self._generation or not self.is_connected:
            return
        try:
            self.pingResultReady.emit(ms)
        except RuntimeError:
            log.debug("Ping result dropped: manager is shutting down")