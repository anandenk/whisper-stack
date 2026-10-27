"""Wire encoding shared by the overlapped voice stream and its clients."""
import json


def encode_event(kind, **values):
    return (json.dumps({"type": kind, **values}, separators=(",", ":")) + "\n").encode()
