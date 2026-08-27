#!/usr/bin/env bash
# Which Legistar jurisdictions actually answer? Only 2 of 5 tested did.
# Keep the 200s. You want 4-6. Do NOT fan out to hundreds — there is no
# published rate limit, nyc already 403s, and a block during the Sep 15-Oct 8
# judging window would take the demo down.
set -u
CANDIDATES="seattle alameda oakland sanjose longbeach mountainview sfgov berkeley
            sanjoseca chicago philadelphia austin denver portlandor kingcounty
            metro sanmateocounty santaclara sacramento fresno"
echo "checking..."
GOOD=""
for c in $CANDIDATES; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "https://webapi.legistar.com/v1/$c/Bodies?\$top=1" 2>/dev/null)
  printf "%-18s %s\n" "$c" "$code"
  [ "$code" = "200" ] && GOOD="$GOOD \"$c\","
done
echo
echo "Put these in WATCHED_CLIENTS in backend/app/tools/legistar.py:"
echo "WATCHED_CLIENTS = ($GOOD)"
