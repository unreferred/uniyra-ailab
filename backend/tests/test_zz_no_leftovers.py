"""Final integrity check: every throwaway owner created by the offline suite
must have been purged. Runs last by file ordering (``zz``).
"""
from __future__ import annotations

from _support import ALLOCATED_OWNERS, TRACKED_OWNERS, owner_record_counts


def test_no_throwaway_leftovers(app_client):
    assert not TRACKED_OWNERS, f"owners still pending purge: {sorted(TRACKED_OWNERS)}"

    async def _scan():
        leftovers = {}
        for owner_id in list(ALLOCATED_OWNERS):
            counts = await owner_record_counts(owner_id)
            if counts:
                leftovers[owner_id] = counts
        return leftovers

    leftovers = app_client.portal.call(_scan)
    assert leftovers == {}, f"throwaway records left behind: {leftovers}"
