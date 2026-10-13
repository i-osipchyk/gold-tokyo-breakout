"""Where DayRecords live: ``<env>/records/YYYY/MM/DD.json`` in S3, or under a local directory."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Protocol

from .record import DayRecord


def record_key(env: str, day: date) -> str:
    return f"{env}/records/{day:%Y/%m/%d}.json"


class RecordStore(Protocol):
    def get(self, day: date) -> DayRecord | None: ...

    def put(self, record: DayRecord) -> None: ...


class DirRecordStore:
    """Records as files under ``root`` (local runs), same layout as the S3 keys."""

    def __init__(self, root: Path, env: str = "dev") -> None:
        self.root, self.env = Path(root), env

    def _path(self, day: date) -> Path:
        return self.root / record_key(self.env, day)

    def get(self, day: date) -> DayRecord | None:
        path = self._path(day)
        return DayRecord.from_json(path.read_text()) if path.exists() else None

    def put(self, record: DayRecord) -> None:
        path = self._path(record.date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.to_json())


class S3RecordStore:
    """Records in a (versioned) S3 bucket: ``s3://<bucket>/<env>/records/YYYY/MM/DD.json``."""

    def __init__(self, client: Any, bucket: str, env: str) -> None:
        self.client, self.bucket, self.env = client, bucket, env

    def get(self, day: date) -> DayRecord | None:
        try:
            res = self.client.get_object(Bucket=self.bucket, Key=record_key(self.env, day))
        except self.client.exceptions.NoSuchKey:
            return None
        return DayRecord.from_json(res["Body"].read().decode())

    def put(self, record: DayRecord) -> None:
        self.client.put_object(Bucket=self.bucket, Key=record_key(self.env, record.date),
                               Body=record.to_json().encode(), ContentType="application/json")
