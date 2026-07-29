"""Camera integration for RTSP and snapshots."""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import urlretrieve

from server_pi.common.models import CameraInfo, EventRecord, EventType

logger = logging.getLogger(__name__)


class CameraService:
    """Handles camera stream configuration and snapshot jobs."""

    def __init__(
        self,
        camera_id: str,
        zone: str,
        rtsp_url: str | None,
        snapshot_source_url: str | None,
        snapshot_dir: Path,
        snapshot_retention_days: int,
    ) -> None:
        self._camera_id = camera_id
        self._zone = zone
        self._rtsp_url = rtsp_url
        self._snapshot_source_url = snapshot_source_url
        self._snapshot_dir = snapshot_dir
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._retention_days = snapshot_retention_days

    def info(self) -> CameraInfo:
        """Build camera info response."""
        snapshot = self.latest_snapshot()
        return CameraInfo(
            camera_id=self._camera_id,
            zone=self._zone,
            stream_url=self._rtsp_url,
            latest_snapshot_url=snapshot[0],
            latest_snapshot_ts=snapshot[1],
        )

    def latest_snapshot(self) -> tuple[str | None, datetime | None]:
        """Return path for latest snapshot if available."""
        files = sorted(self._snapshot_dir.glob("*.jpg"))
        if not files:
            return None, None
        latest = files[-1]
        ts = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
        return f"/snapshots/{latest.name}", ts

    def capture_snapshot(self) -> EventRecord | None:
        """Capture snapshot and return an error event on failure."""
        target = self._snapshot_dir / f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}.jpg"
        try:
            if self._snapshot_source_url:
                urlretrieve(self._snapshot_source_url, target)
            elif self._rtsp_url:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-rtsp_transport",
                        "tcp",
                        "-i",
                        self._rtsp_url,
                        "-frames:v",
                        "1",
                        "-y",
                        str(target),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=20,
                )
            else:
                return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("camera_snapshot_failed")
            return EventRecord(
                ts=datetime.now(UTC),
                zone=self._zone,
                device_id=self._camera_id,
                type=EventType.camera,
                level="error",
                message="snapshot capture failed",
                actor="camera-service",
                metadata={"error": str(exc)},
            )
        self.cleanup_old_snapshots()
        return None

    def cleanup_old_snapshots(self) -> None:
        """Remove snapshots older than retention policy."""
        limit = datetime.now(tz=UTC) - timedelta(days=self._retention_days)
        for candidate in self._snapshot_dir.glob("*.jpg"):
            ts = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
            if ts < limit:
                candidate.unlink(missing_ok=True)
