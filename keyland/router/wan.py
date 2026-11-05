"""WAN health tracking and active-WAN selection (pure logic, easy to test)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import RouterConfig

TABLE_ID = 100
RULE_PRIORITY = 10050


@dataclass
class WanState:
    name: str                      # "wifi" | "sim"
    iface: str = ""
    present: bool = False
    ipv4: str | None = None
    gateway: str | None = None
    healthy: bool | None = None    # None = not probed yet
    latency_ms: float | None = None
    fails: int = 0
    successes: int = 0
    weight: int = 1
    extra: dict = field(default_factory=dict)

    def routable(self) -> bool:
        return self.present and self.ipv4 is not None

    def public(self) -> dict:
        return {
            "name": self.name, "iface": self.iface, "present": self.present,
            "ipv4": self.ipv4, "gateway": self.gateway, "healthy": self.healthy,
            "latency_ms": self.latency_ms, "weight": self.weight,
        }


def record_probe(ws: WanState, ok: bool, latency: float | None, cfg: RouterConfig) -> None:
    """Hysteresis: N failures to go down, M successes to come back."""
    ws.latency_ms = latency if ok else None
    if ok:
        ws.successes += 1
        ws.fails = 0
        if ws.healthy is None or (not ws.healthy and ws.successes >= cfg.recover_threshold):
            ws.healthy = True
    else:
        ws.fails += 1
        ws.successes = 0
        if ws.healthy is None or (ws.healthy and ws.fails >= cfg.fail_threshold):
            ws.healthy = False


def choose_active(cfg: RouterConfig, wans: dict[str, WanState]) -> list[str]:
    """Names of WANs that should carry hotspot traffic, best first."""
    other = "sim" if cfg.primary == "wifi" else "wifi"
    usable = {n: w for n, w in wans.items() if w.routable()}

    if cfg.wan_mode == "wifi_only":
        return ["wifi"] if "wifi" in usable else []
    if cfg.wan_mode == "sim_only":
        return ["sim"] if "sim" in usable else []

    good = [n for n, w in usable.items() if w.healthy]
    if cfg.wan_mode == "balance":
        if good:
            return sorted(good, key=lambda n: n != cfg.primary)
        return [n for n in (cfg.primary, other) if n in usable][:1]

    # failover
    for n in (cfg.primary, other):
        if n in good:
            return [n]
    # nothing healthy: keep primary if it exists (probes may recover), else the other
    for n in (cfg.primary, other):
        if n in usable:
            return [n]
    return []


def build_route_cmds(cfg: RouterConfig, active: list[WanState]) -> list[list[str]]:
    """`ip` commands that program policy-routing table for hotspot clients only."""
    t = str(TABLE_ID)
    cmds = [["ip", "route", "replace", cfg.subnet, "dev", cfg.ap_iface, "table", t]]

    def hop(w: WanState) -> list[str]:
        h = ["via", w.gateway] if w.gateway else []
        return h + ["dev", w.iface]

    if not active:
        cmds.append(["ip", "route", "replace", "unreachable", "default", "table", t])
    elif len(active) == 1:
        cmds.append(["ip", "route", "replace", "default", *hop(active[0]), "table", t])
    else:
        multi = ["ip", "route", "replace", "default", "table", t]
        for w in active:
            multi += ["nexthop", *hop(w), "weight", str(w.weight)]
        cmds.append(multi)
    return cmds
