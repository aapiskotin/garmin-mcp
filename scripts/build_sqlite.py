"""Build the WAL-fixed SQLite library from checksum-verified upstream source."""

import hashlib
import io
import subprocess
import urllib.request
import zipfile
from pathlib import Path

URL = "https://www.sqlite.org/2026/sqlite-amalgamation-3510300.zip"
# https://www.sqlite.org/releaselog/3_51_3.html
C_SHA3 = "32d5424f97e0a7fc5ed2f6335afbb58be4e0298bd7117a34e39d345ff13d859e"

with urllib.request.urlopen(URL, timeout=60) as response:
    archive = zipfile.ZipFile(io.BytesIO(response.read()))
source = archive.read("sqlite-amalgamation-3510300/sqlite3.c")
if hashlib.sha3_256(source).hexdigest() != C_SHA3:
    raise SystemExit("SQLite source checksum mismatch")
Path("sqlite3.c").write_bytes(source)
subprocess.run(
    [
        "cc",
        "-O2",
        "-fPIC",
        "-shared",
        "-DSQLITE_THREADSAFE=1",
        "-DSQLITE_ENABLE_FTS5",
        "-DSQLITE_ENABLE_RTREE",
        "-DSQLITE_ENABLE_COLUMN_METADATA",
        "-Wl,-soname,libsqlite3.so.0",
        "sqlite3.c",
        "-o",
        "/libsqlite3.so.0",
        "-lm",
        "-ldl",
    ],
    check=True,
)
