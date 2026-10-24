"""Strict aggregate contract for live dashboard transport observations."""

FIELDS = {
    "schema_version",
    "active_clients",
    "max_concurrent_clients",
    "rejected_clients_total",
    "expired_clients_total",
}


def validate_live_transport(value: object) -> dict[str, int]:
    """Validate and sanitize one response sampled through ``/transport.json``.

    The handler increments the active-client count before parsing or serving a
    request and decrements it only after the response finishes.  Therefore the
    response that carries this observation is itself evidence of at least one
    active client.  Accepting zero would certify telemetry that cannot describe
    the live request which produced it.
    """
    if type(value) is not dict or set(value) != FIELDS:
        raise ValueError("transport schema")
    if type(value.get("schema_version")) is not int \
            or value["schema_version"] != 1:
        raise ValueError("transport schema")
    for key in FIELDS - {"schema_version"}:
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("transport count")
    if not 1 <= value["max_concurrent_clients"] <= 256 \
            or not 1 <= value["active_clients"] <= value["max_concurrent_clients"]:
        raise ValueError("transport capacity")
    return {key: value[key] for key in (
        "active_clients", "max_concurrent_clients", "rejected_clients_total",
        "expired_clients_total",
    )}
