"""Bind numeric site codes to independent author labels by stable IDs."""
from .participants import numeric


def resolve_site_map(values, labels):
    if len(values) != len(labels):
        raise ValueError("Labelled/numeric record count differs")
    labelled = {}
    for row in labels:
        key = row["session_id"].strip()
        if not key or key in labelled:
            raise ValueError("Missing or duplicate labelled session_id")
        labelled[key] = row
    seen = set()
    mapping = {}
    for row in values:
        key = row["session_id"].strip()
        if not key or key in seen or key not in labelled:
            raise ValueError("Missing, duplicate or unmatched numeric session_id")
        seen.add(key)
        other = labelled[key]
        for field in ("sunkDV", "sunkcosta", "sunkcostb"):
            if numeric(row[field]) != numeric(other[field]):
                raise ValueError("Labelled/numeric response differs")
        condition = numeric(row["sunkgroup"])
        expected = {0: "paid", 1: "free", None: ""}.get(condition)
        if expected is None or other["sunkgroup"].strip() != expected:
            raise ValueError("Labelled/numeric condition differs")
        code = numeric(row["sample"])
        if code is None or code != int(code) or not 1 <= code <= 36:
            raise ValueError("Invalid numeric site code")
        name = other["sample"].strip()
        if not name or not name.isascii():
            raise ValueError("Invalid site label")
        code = int(code)
        if code in mapping and mapping[code] != name:
            raise ValueError("One site code maps to multiple labels")
        mapping[code] = name
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Multiple site codes map to one label")
    return mapping
