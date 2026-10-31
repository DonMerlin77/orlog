"""
orlog/app/deploy.py — Build + deploy Orlog with JS minification.

Minifies the JS in index.html before deploying to Modal so the
client-side code is harder to read. Source files are never modified —
a build copy is created, deployed, then cleaned up.

Usage:
    cd ~/shopify_store && source venv/bin/activate
    python3 orlog/app/deploy.py
"""

import re
import shutil
import subprocess
from pathlib import Path

APP_DIR     = Path(__file__).parent
STATIC_DIR  = APP_DIR / "static"
BUILD_DIR   = APP_DIR / "static_build"


def _minify_js(js: str) -> str:
    """Strip comments and collapse whitespace — basic obfuscation."""
    # Remove single-line comments (but not URLs)
    js = re.sub(r'(?<!:)//(?!.*https?:).*', '', js)
    # Remove multi-line comments
    js = re.sub(r'/\*.*?\*/', '', js, flags=re.DOTALL)
    # Collapse whitespace (but not inside strings — simple approximation)
    js = re.sub(r'\n\s*\n', '\n', js)
    js = re.sub(r'  +', ' ', js)
    return js.strip()


def _process_html(html: str) -> str:
    """Extract inline <script> blocks, minify them, return processed HTML."""
    def minify_script(match):
        tag_open = match.group(1)
        content  = match.group(2)
        tag_close = match.group(3)
        return tag_open + _minify_js(content) + tag_close

    return re.sub(
        r'(<script(?:[^>]*)>)(.*?)(</script>)',
        minify_script,
        html,
        flags=re.DOTALL,
    )


def build():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    shutil.copytree(STATIC_DIR, BUILD_DIR)

    for html_file in BUILD_DIR.glob("*.html"):
        original = html_file.read_text(encoding="utf-8")
        processed = _process_html(original)
        html_file.write_text(processed, encoding="utf-8")
        original_size  = len(original.encode())
        processed_size = len(processed.encode())
        saved = round((1 - processed_size / original_size) * 100, 1)
        print(f"  {html_file.name}: {original_size//1024}KB → {processed_size//1024}KB ({saved}% smaller)")

    print(f"Build complete: {BUILD_DIR}")
    return BUILD_DIR


def deploy():
    print("Building...")
    build()

    # Temporarily swap static for static_build for the Modal deploy
    static_backup = APP_DIR / "static_orig_backup"
    STATIC_DIR.rename(static_backup)
    BUILD_DIR.rename(STATIC_DIR)

    try:
        print("Deploying to Modal...")
        result = subprocess.run(
            ["modal", "deploy", str(APP_DIR / "modal_web.py")],
            capture_output=False,
        )
    finally:
        # Always restore original static dir
        STATIC_DIR.rename(BUILD_DIR)
        static_backup.rename(STATIC_DIR)
        shutil.rmtree(BUILD_DIR, ignore_errors=True)

    if result.returncode == 0:
        print("Deployed. Source files unchanged.")
    else:
        print("Deploy failed — source files restored.")


if __name__ == "__main__":
    deploy()
