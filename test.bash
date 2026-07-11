# readyz up while DB is healthy
curl -i localhost:8000/readyz          # expect 200

# now KILL Postgres and watch readyz flip
docker compose -f deploy/compose/docker-compose.yml stop postgres
curl -i localhost:8000/readyz          # expect 503 — the DB is gone
curl -i localhost:8000/healthz         # expect STILL 200 — liveness doesn't care about the DB

# bring it back
docker compose -f deploy/compose/docker-compose.yml start postgres
# (wait a few seconds for it to accept connections)
sleep 10
curl -i localhost:8000/readyz          # expect 200 again — recovered without a restart


curl -s -X POST localhost:8000/api/urls -H 'content-type: application/json' -d '{"long_url":"https://example.com/x"}'
docker compose -f deploy/compose/docker-compose.yml logs api | tail -5
# expect JSON lines, each with a request_id, method, path, status, duration