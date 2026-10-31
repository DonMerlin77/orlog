"""
orlog/app/pull_accounts.py — ONE-SHOT. List Clerk accounts via API.

Shows who signed up — email, name, created date.
Never touches question logs. Identity and questions are always separate.

Usage:
    modal run orlog/app/pull_accounts.py
"""
import modal

app = modal.App("orlog-pull-accounts")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("requests>=2.31.0")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("orlog-secrets")],
)
def pull_users():
    import os
    import requests

    clerk_key = os.getenv("CLERK_SECRET_KEY", "")
    if not clerk_key:
        return {"error": "CLERK_SECRET_KEY not found in orlog-secrets"}

    users = []
    offset = 0
    limit = 100

    while True:
        resp = requests.get(
            "https://api.clerk.com/v1/users",
            headers={"Authorization": f"Bearer {clerk_key}"},
            params={"limit": limit, "offset": offset, "order_by": "-created_at"},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break

        for u in batch:
            email = ""
            if u.get("email_addresses"):
                email = u["email_addresses"][0].get("email_address", "")
            first = u.get("first_name") or ""
            last = u.get("last_name") or ""
            name = f"{first} {last}".strip() or "(no name)"
            created = u.get("created_at", 0)
            import datetime
            ts = datetime.datetime.fromtimestamp(created / 1000).strftime("%Y-%m-%d %H:%M")
            users.append({"name": name, "email": email, "created": ts, "id": u["id"]})

        if len(batch) < limit:
            break
        offset += limit

    return users


@app.local_entrypoint()
def main():
    users = pull_users.remote()

    if isinstance(users, dict) and "error" in users:
        print(f"Error: {users['error']}")
        return

    print(f"\n{'='*60}")
    print(f"ORLOG ACCOUNTS ({len(users)} total)")
    print(f"{'='*60}")
    for u in users:
        print(f"  {u['created']}  {u['name']:<20}  {u['email']}")

    print(f"\n{len(users)} account(s) total.")
