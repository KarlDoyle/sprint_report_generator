"""Generic secret scan (pytest port of the ai-starter standard secret-scan test).

Fails if anything credential-shaped has been committed. The scan roots are the
dirs this repo commits; missing dirs are skipped, so this drops into any repo
unchanged. Pattern-based on purpose: cheap, deterministic, catches the shapes
that actually leak. `.env.example` files are scanned too and must only ever hold
empty or obvious-placeholder values.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = ["sprint_report_generator", "forge-app", "tests", "scripts", "docs", ".github"]
SELF = Path(__file__).resolve()

TOKEN_PATTERNS = [
    ("anthropic api key", re.compile(r"\bsk-ant-[A-Za-z0-9-]{20,}\b")),
    ("openai-style api key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github personal access token (classic)", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("github fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("github oauth/server token", re.compile(r"\bgh[ous]_[A-Za-z0-9]{30,}\b")),
    ("slack bot token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

SKIP_SUFFIXES = (".lock", ".lockb", ".pyc")
SKIP_FILES = {"pnpm-lock.yaml", "package-lock.json", "yarn.lock", "uv.lock"}


def _scannable_files():
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.resolve() == SELF:
                continue
            if path.name in SKIP_FILES or path.suffix in SKIP_SUFFIXES:
                continue
            if "__pycache__" in path.parts:
                continue
            yield path
    # Always scan root-level dotenv files — the highest-risk place a key leaks,
    # and the one this scanner claims to cover. SCAN_ROOTS only lists sub-dirs,
    # so without this a key in a committed root `.env.example` is missed.
    for path in REPO_ROOT.glob(".env*"):
        if path.is_file() and path.suffix not in SKIP_SUFFIXES:
            yield path


def test_no_committed_secrets():
    findings = []
    for path in _scannable_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in TOKEN_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append((str(path.relative_to(REPO_ROOT)), name, match.group(0)[:12] + "…"))
    assert findings == [], f"possible secrets committed: {findings}"
