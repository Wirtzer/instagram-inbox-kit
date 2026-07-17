"""Point the package at a throwaway data dir BEFORE ig_inbox.config is imported,
so tests never touch a real install's data/state/logs."""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="ig_inbox_test_")
os.environ.setdefault("IG_INBOX_HOME", _TMP)
os.environ.setdefault("IG_INBOX_DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("IG_INBOX_STATE_DIR", os.path.join(_TMP, "state"))
os.environ.setdefault("IG_INBOX_LOG_DIR", os.path.join(_TMP, "logs"))
os.environ.setdefault("IG_INBOX_CRED_DIR", os.path.join(_TMP, "cred"))
