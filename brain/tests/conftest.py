import asyncio
import os
import sys
import warnings

# Tests never need real secrets; a fixed dummy key keeps crypto tests hermetic.
os.environ.setdefault("PII_ENCRYPTION_KEY", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")

# psycopg's async pool cannot run on Windows' default ProactorEventLoop. Linux (CI, Docker) is unaffected.
# shortcut: the policy API is deprecated in Python 3.14; drop this when psycopg supports Proactor.
if sys.platform == "win32":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
