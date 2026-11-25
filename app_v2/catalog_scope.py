"""Visibility of signed catalog records across a workbench reset."""
from pyoxigraph import NamedNode

from .graph import AUDIT, PROV, iri


def reset_sequence(catalog):
    boundary = catalog.get("workspace_reset")
    return boundary["sequence_after"] if boundary else 0


def record_visible(catalog, identity, boundary):
    if not boundary:
        return True
    base, separator, revision = identity.rpartition("/revision/")
    if separator and revision.isdigit():
        if int(revision) < boundary:
            return False
        identity = base
    description = iri("record/" + identity)
    revisions = catalog.store.quads_for_pattern(None, NamedNode(PROV + "specializationOf"), description, AUDIT)
    versions = [int(q.subject.value.rsplit("/", 1)[-1]) for q in revisions]
    return bool(versions) and min(versions) >= boundary
