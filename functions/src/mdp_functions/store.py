"""Manifest-last object storage. Object names are relative to an explicit root."""

import os
import uuid
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import boto3

from mdp_functions.fetch.guard import transport_network
from mdp_functions.settings import Settings


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> list[str]: ...
    # Retention (retention.py) removes objects past a function's retain_days; a missing key is no error.
    def delete(self, key: str) -> None: ...
    def uri(self, key: str) -> str: ...
    def health(self) -> None: ...


class LocalFsStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Object path escapes store")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)

    def get(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def list(self, prefix: str) -> list[str]:
        return sorted(
            str(p.relative_to(self.root))
            for p in self.path(prefix).rglob("*")
            if p.is_file()
        )

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def uri(self, key: str) -> str:
        return self.path(key).as_uri()

    def health(self) -> None:
        if not os.access(self.root, os.R_OK | os.W_OK):
            raise OSError("Dump root is not readable and writable")


class S3Store:
    @transport_network()
    def __init__(self, settings: Settings) -> None:
        url = urlparse(settings.dump_root)
        self.bucket = settings.r2_bucket or url.netloc
        self.prefix = url.path.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint
            or f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    def key(self, key: str) -> str:
        if ".." in Path(key).parts or key.startswith("/"):
            raise ValueError("Invalid object key")
        return "/".join(p for p in (self.prefix, key) if p)

    @transport_network()
    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self.key(key), Body=data)

    @transport_network()
    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self.key(key))[
            "Body"
        ].read()

    @transport_network()
    def list(self, prefix: str) -> list[str]:
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self.key(prefix)
        )
        return [
            o["Key"][len(self.prefix) + 1 :] if self.prefix else o["Key"]
            for p in pages
            for o in p.get("Contents", [])
        ]

    @transport_network()
    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.key(key))

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self.key(key)}"

    @transport_network()
    def health(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


def make_store(settings: Settings) -> ObjectStore:
    if settings.dump_root.startswith("file://"):
        return LocalFsStore(urlparse(settings.dump_root).path)
    if settings.dump_root.startswith("s3://"):
        return S3Store(settings)
    raise ValueError("MDP_DUMP_ROOT must be file:// or s3://")
