"""Airport graph resolver: lookup walking times from airport JSON graph files."""

import json
import math
from pathlib import Path

_graph_cache: dict[str, dict | None] = {}
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "airports"


def _load_graph(airport_iata: str) -> dict | None:
    key = airport_iata.upper()
    if key in _graph_cache:
        return _graph_cache[key]
    path = _DATA_DIR / f"{key}.json"
    if not path.exists():
        _graph_cache[key] = None
        return None
    with open(path) as f:
        data = json.load(f)
    _graph_cache[key] = data
    return data


def _build_adjacency(edges: list) -> dict[str, dict[str, int]]:
    adj: dict[str, dict[str, int]] = {}
    for from_node, to_node, minutes in edges:
        adj.setdefault(from_node, {})[to_node] = minutes
        adj.setdefault(to_node, {})[from_node] = minutes
    return adj


def _find_gate_cluster(nodes: dict, gate: str) -> str | None:
    for node_id, node in nodes.items():
        if node.get("type") == "gates" and gate in (node.get("gates") or []):
            return node_id
    return None


def _find_node_by_type(nodes: dict, node_type: str) -> str | None:
    for node_id, node in nodes.items():
        if node.get("type") == node_type:
            return node_id
    return None


def _find_parking_node(nodes: dict) -> str | None:
    for node_id, node in nodes.items():
        if node.get("type") == "parking":
            return node_id
    return None


def _edge_weight(adj: dict[str, dict[str, int]], from_node: str, to_node: str) -> int | None:
    return adj.get(from_node, {}).get(to_node)


def resolve_walking_times(
    airport_iata: str,
    transport_mode: str,
    terminal: str | None = None,
    gate: str | None = None,
    with_children: bool = False,
) -> dict | None:
    graph = _load_graph(airport_iata)
    if graph is None:
        return None

    nodes = graph["nodes"]
    edges = graph["edges"]
    defaults = graph["defaults"]

    # Determine terminal
    term = terminal or defaults["terminal"]

    # Determine entry node
    if transport_mode in ("rideshare", "taxi", "other"):
        entry_node = f"curb:{term}"
    elif transport_mode == "driving":
        entry_node = _find_parking_node(nodes)
    elif transport_mode in ("transit", "train", "bus"):
        entry_node = _find_node_by_type(nodes, "transit")
    else:
        entry_node = f"curb:{term}"

    # If entry node doesn't exist, return defaults
    if entry_node is None or entry_node not in nodes:
        return {
            "entry_to_checkin": defaults["curb_to_checkin"],
            "checkin_to_tsa": defaults["checkin_to_security"],
            "tsa_to_gate": defaults["security_to_gate_median"],
            "source": "fallback",
        }

    adj = _build_adjacency(edges)

    checkin_node = f"checkin:{term}"
    tsa_node = f"tsa:{term}"

    # entry → checkin
    entry_to_checkin = _edge_weight(adj, entry_node, checkin_node)
    if entry_to_checkin is None:
        entry_to_checkin = defaults["curb_to_checkin"]

    # checkin → tsa
    checkin_to_tsa = _edge_weight(adj, checkin_node, tsa_node)
    if checkin_to_tsa is None:
        checkin_to_tsa = defaults["checkin_to_security"]

    # tsa → gate
    tsa_to_gate = None
    if gate:
        cluster_id = _find_gate_cluster(nodes, gate)
        if cluster_id:
            tsa_to_gate = _edge_weight(adj, tsa_node, cluster_id)
    if tsa_to_gate is None:
        tsa_to_gate = defaults["security_to_gate_median"]

    # Children multiplier
    if with_children:
        entry_to_checkin = math.ceil(entry_to_checkin * 1.4)
        checkin_to_tsa = math.ceil(checkin_to_tsa * 1.4)
        tsa_to_gate = math.ceil(tsa_to_gate * 1.4)

    return {
        "entry_to_checkin": entry_to_checkin,
        "checkin_to_tsa": checkin_to_tsa,
        "tsa_to_gate": tsa_to_gate,
        "source": "graph",
    }
