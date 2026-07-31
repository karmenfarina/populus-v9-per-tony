"""Create two test users and print their tokens/user_ids for the Playwright run.

1) Google-flipped user: anonymous signup → mongo-flip to auth_provider=google,
   is_anonymous=False, onboarding_completed=False.
2) Anonymous regression user: anonymous signup → mongo-flip
   onboarding_completed to False (keep auth_provider=anonymous).

Outputs JSON to stdout with both tokens + user_ids so the caller can clean up.
"""
import json
import uuid
import sys
import requests
from pymongo import MongoClient

BASE_URL = "https://voti-scroll-fix.preview.emergentagent.com"
MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"


def create_anon(prefix: str):
    nick = f"{prefix}{uuid.uuid4().hex[:8]}"
    r = requests.post(f"{BASE_URL}/api/auth/anonymous", json={"nickname": nick}, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data["token"], data["user"]["user_id"], nick


def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # Google user
    g_token, g_uid, g_nick = create_anon("gtest_")
    db.users.update_one(
        {"user_id": g_uid},
        {"$set": {
            "auth_provider": "google",
            "is_anonymous": False,
            "onboarding_completed": False,
            "email": f"{g_uid}@example.com",
            "name": "Google User Test",
        }},
    )

    # Anon regression user
    a_token, a_uid, a_nick = create_anon("atest_")
    db.users.update_one(
        {"user_id": a_uid},
        {"$set": {"onboarding_completed": False}},
    )

    print(json.dumps({
        "google": {"token": g_token, "user_id": g_uid, "nickname": g_nick},
        "anon": {"token": a_token, "user_id": a_uid, "nickname": a_nick},
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
