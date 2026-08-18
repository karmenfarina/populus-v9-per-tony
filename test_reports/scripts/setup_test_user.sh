#!/bin/bash
# Helper to prepare a test user with votes for collapsible-section testing.
set -e
BASE="https://feud-admin-panel.preview.emergentagent.com"
EMAIL="TEST_collap_$(date +%s)@test.com"
PASS="Passw0rd!"
NICK="collap$(date +%s | tail -c 5)"

# Signup
RESP=$(curl -s -X POST "$BASE/api/auth/signup" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"nickname\":\"$NICK\"}")
TOKEN=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
USER_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['user']['user_id'])")

# Onboarding
curl -s -X PATCH "$BASE/api/auth/me/profile" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"age":28,"sex":"M","region":"Lombardia","favorite_categories":["politica","musica"]}' >/dev/null

# Cast 3 votes
FEUDS=$(curl -s "$BASE/api/feuds" | python3 -c "import sys,json; d=json.load(sys.stdin); f=d.get('feuds', d); print(' '.join([x['feud_id'] for x in f[:3]]))")
i=0
for FID in $FEUDS; do
  SIDE=$([ $((i%2)) -eq 0 ] && echo "A" || echo "B")
  curl -s -X POST "$BASE/api/feuds/$FID/vote" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -d "{\"side\":\"$SIDE\"}" >/dev/null
  i=$((i+1))
done

# Anon user for regression test 2f
ANON_RESP=$(curl -s -X POST "$BASE/api/auth/anonymous" -H 'Content-Type: application/json' \
  -d "{\"nickname\":\"ANONcollap$(date +%s | tail -c 5)\"}")
ANON_ID=$(echo "$ANON_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['user']['user_id'])")
ANON_TOKEN=$(echo "$ANON_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

echo "TOKEN=$TOKEN"
echo "USER_ID=$USER_ID"
echo "ANON_ID=$ANON_ID"
echo "ANON_TOKEN=$ANON_TOKEN"
