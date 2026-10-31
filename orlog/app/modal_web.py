"""
orlog/app/modal_web.py — Deploy Orlog FastAPI app to Modal.

Deploy:
    modal deploy orlog/app/modal_web.py

Serves the full Orlog app at a public HTTPS URL.
"""
import modal
from pathlib import Path

app = modal.App("orlog-web")
_BUILD = "2026-06-04-streaming"  # bump to force image rebuild

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.110.0",
        "uvicorn>=0.29.0",
        "anthropic>=0.40.0",
        "modal>=0.64.0",
        "python-dotenv>=1.0.0",
        "PyJWT>=2.8.0",
        "cryptography>=42.0.0",
        "stripe>=8.0.0",
    )
    .add_local_dir(
        str(Path(__file__).parent),
        remote_path="/app",
        ignore=["user_data"],
    )
    .add_local_dir(
        str(Path(__file__).parent.parent),
        remote_path="/project/orlog",
    )
)

# Persistent volume for user history (survives redeployments)
user_vol = modal.Volume.from_name("orlog-user-data", create_if_missing=True)

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("orlog-secrets")],
    volumes={"/app/user_data": user_vol},
    min_containers=0,   # sleep when idle (2026-10-01: always-warm cost ~$30/mo with no visitors)
    max_containers=50,
    timeout=300,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def serve():
    import sys
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/project")
    from main import app as fastapi_app
    return fastapi_app
