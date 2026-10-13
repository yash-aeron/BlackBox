"""Browser permission policy.

BlackBox grants nothing it does not need.  Sensitive capabilities are explicitly
denied so that a website cannot pull the agent into camera, microphone,
geolocation or payment surfaces, and downloads are funnelled into a known
directory that the agent treats as an observed artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Capabilities an ordinary page might request that BlackBox never needs.
DENIED_PERMISSIONS = (
    "geolocation",
    "notifications",
    "camera",
    "microphone",
    "midi",
    "midiSysex",
    "background-sync",
    "payment-handler",
    "idle-detection",
    "storage-access",
    "durable-storage",
    "window-management",
    "local-fonts",
    "top-level-storage-access",
)

# Capabilities that are safe and sometimes needed for a page to behave normally.
GRANTABLE_PERMISSIONS = (
    "clipboard-read",
    "clipboard-write",
)


@dataclass
class PermissionPolicy:
    downloads_dir: Path
    allow_downloads: bool = True
    grant: tuple[str, ...] = ()
    deny: tuple[str, ...] = DENIED_PERMISSIONS
    emulate_media_print: bool = False

    def desktop_params(self) -> dict[str, object]:
        """Extra browser-level setup applied once per browser launch."""
        return {
            "downloadPath": str(self.downloads_dir),
            "allow": self.allow_downloads,
        }

    def permission_entries(self, origin: str) -> list[dict[str, object]]:
        """``Browser.setPermission`` entries for one origin."""
        entries: list[dict[str, object]] = []
        for name in self.deny:
            entries.append({"origin": origin, "permission": {"name": name}, "setting": "denied"})
        for name in self.grant:
            if name in GRANTABLE_PERMISSIONS:
                entries.append({"origin": origin, "permission": {"name": name}, "setting": "granted"})
        return entries


def default_policy(downloads_dir: Path, *, allow_downloads: bool = True) -> PermissionPolicy:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    return PermissionPolicy(downloads_dir=downloads_dir, allow_downloads=allow_downloads)
