"""Run deterministic browser regressions against local Supabase; no vendor calls.

Run from apps/api: uv run python scripts/run_browser_tests.py
Requires local migrations applied, pnpm dependencies and Playwright Chromium.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
from tests._local_supabase import LOCAL_SUPABASE_URL, LOCAL_SUPABASE_ANON_KEY, LOCAL_SUPABASE_SERVICE_ROLE_KEY


def main() -> int:
    env = dict(os.environ, SUPABASE_URL=LOCAL_SUPABASE_URL,
        SUPABASE_SERVICE_ROLE_KEY=LOCAL_SUPABASE_SERVICE_ROLE_KEY,
        GEMINI_API_KEY="dummy", VOYAGE_API_KEY="dummy", COHERE_API_KEY="",
        INGEST_WORKER_ENABLED="0", SENTRY_DSN="", NEXT_PUBLIC_SENTRY_DSN="",
        NEXT_PUBLIC_SUPABASE_URL=LOCAL_SUPABASE_URL,
        NEXT_PUBLIC_SUPABASE_ANON_KEY=LOCAL_SUPABASE_ANON_KEY,
        NEXT_PUBLIC_API_URL="http://127.0.0.1:8000", NEXT_TELEMETRY_DISABLED="1",
        FRONTEND_ORIGINS="http://127.0.0.1:3000,http://localhost:3000")
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"], cwd=API_DIR, env=env)
    try:
        for _ in range(60):
            if api.poll() is not None:
                raise RuntimeError("Test API exited before becoming ready")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("Test API did not become ready")
        pnpm = "pnpm.cmd" if os.name == "nt" else "pnpm"
        return subprocess.call([pnpm, "exec", "playwright", "test", "e2e/workspaces.e2e.ts",
            "e2e/document-actions.e2e.ts", "e2e/chat-modernization.e2e.ts", "e2e/page-preview.e2e.ts",
            "--grep", "private workspaces|test-user cleanup|document selection|item 7|opens the cited"],
            cwd=API_DIR.parent / "web", env=env)
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait()


if __name__ == "__main__":
    sys.exit(main())
