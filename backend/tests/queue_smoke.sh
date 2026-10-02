#!/usr/bin/env bash
# Curl verification of the job queue + settings endpoints (local :9090 stub only).
# Usage: backend/tests/queue_smoke.sh   (env B=http://127.0.0.1:8001/api). Needs jq.
# Creates ~7 jobs (titles q_A..q_F), deletes them from Mongo at the end (needs mongosh)
# and resets settings to the .env values.
B=${B:-http://127.0.0.1:8001/api}; ST=http://127.0.0.1:9090
PASS=0; FAIL=0; IDS=()
ok(){ if [ "$1" = "$2" ]; then PASS=$((PASS+1)); echo "PASS $3"; else FAIL=$((FAIL+1)); echo "FAIL $3: expected [$2] got [$1]"; fi; }
rm_(){ printf '# q_%s\n\n### Step 1\nDo 1.\n\n### Step 2\nDo 2.\n' "$1"; }
mk(){ curl -s -X POST $B/jobs -H 'content-type: application/json' -d "$(jq -n --arg r "$(rm_ $1)" --arg m "${2:-stub-slow}" --arg u $ST '{arena_url:$u,model:$m,roadmap_md:$r}')"; }
order(){ curl -s $B/queue | jq -r '[.queued[] | "\(.title|sub("q_";""))\(if .paused then "(p)" else "" end)"] | join(",")'; }
st(){ curl -s $B/jobs/$1 | jq -r .status; }
code(){ curl -s -o /dev/null -w '%{http_code}' "$@"; }

# --- settings
ok "$(curl -s $B/settings | jq -r '[.arena_url,.model]|join(" ")')" "http://localhost:9090 gpt-4o" "GET /settings seeded from .env"
for body in '{"arena_url":"ftp://x"}' '{"arena_url":""}' '{"model":"  "}' '{"step_delay_seconds":-1}' '{"step_delay_seconds":601}' \
            '{"request_timeout_seconds":9}' '{"request_timeout_seconds":3601}' '{"step_delay_seconds":"5"}' '{"step_delay_seconds":true}' '{"bogus":1}' '[1]'; do
  ok "$(code -X PUT $B/settings -H 'content-type: application/json' -d "$body")" 422 "PUT /settings $body -> 422"
done
ok "$(code -X PUT $B/settings -H 'content-type: application/json' -d '{bad')" 422 "PUT /settings malformed JSON -> 422"
ok "$(curl -s -X PUT $B/settings -H 'content-type: application/json' -d '{"step_delay_seconds":1,"request_timeout_seconds":120,"model":"gpt-4o-mini"}' | jq -c '[.step_delay_seconds,.request_timeout_seconds,.model]')" '[1,120,"gpt-4o-mini"]' "PUT /settings valid partial update"
ok "$(curl -s $B/config | jq -c '[.model,.step_delay_seconds]')" '["gpt-4o-mini",1]' "GET /config reflects settings (form defaults)"
ok "$(curl -s -X PUT $B/settings -H 'content-type: application/json' -d '{"step_delay_seconds":0,"request_timeout_seconds":10}' | jq -c '[.step_delay_seconds,.request_timeout_seconds]')" '[0,10]' "PUT /settings boundaries 0 and 10 accepted"
ok "$(curl -s -X PUT $B/settings -H 'content-type: application/json' -d '{"step_delay_seconds":600,"request_timeout_seconds":3600}' | jq -c '[.step_delay_seconds,.request_timeout_seconds]')" '[600,3600]' "PUT /settings boundaries 600 and 3600 accepted"
ok "$(curl -s -X POST $B/settings/reset | jq -c '[.model,.step_delay_seconds,.request_timeout_seconds]')" '["gpt-4o",2,300]' "POST /settings/reset restores .env values"
curl -s -X PUT $B/settings -H 'content-type: application/json' -d '{"step_delay_seconds":1}' >/dev/null

# --- enqueue
A=$(mk A); ok "$(jq -c '[.status,.queue_position]' <<<"$A")" '["running",null]' "POST /jobs A starts immediately"
A=$(jq -r .job_id <<<"$A"); IDS+=($A)
Bj=$(mk B); ok "$(jq -c '[.status,.queue_position]' <<<"$Bj")" '["queued",1]' "POST /jobs B queued at 1 (201, no 409)"
Bj=$(jq -r .job_id <<<"$Bj"); IDS+=($Bj)
C=$(mk C | jq -r .job_id); IDS+=($C); D=$(mk D | jq -r .job_id); IDS+=($D)
ok "$(order)" "B,C,D" "queue order B,C,D"
ok "$(curl -s $B/queue | jq -r '.running.job_id')" "$A" "GET /queue running = A"
ok "$(curl -s "$B/jobs?status=queued,paused" | jq length)" 3 "GET /jobs?status=queued,paused lists 3"
# --- reorder
ok "$(curl -s -X POST $B/queue/$D/move -H 'content-type: application/json' -d '{"direction":"up"}' | jq -r .queue_position)" 2 "move D up -> 2"
ok "$(order)" "B,D,C" "order B,D,C"
ok "$(curl -s -X POST $B/queue/$C/move -H 'content-type: application/json' -d '{"position":1}' | jq -r .queue_position)" 1 "move C to position 1"
ok "$(order)" "C,B,D" "order C,B,D"
curl -s -X POST $B/queue/$C/move -H 'content-type: application/json' -d '{"direction":"up"}' >/dev/null
ok "$(order)" "C,B,D" "move up at top is a no-op"
curl -s -X POST $B/queue/$D/move -H 'content-type: application/json' -d '{"position":99}' >/dev/null
ok "$(order)" "C,B,D" "position beyond end clamps to last"
ok "$(code -X POST $B/queue/$A/move -H 'content-type: application/json' -d '{"direction":"up"}')" 409 "move running job -> 409"
ok "$(code -X POST $B/queue/$C/move -H 'content-type: application/json' -d '{"direction":"left"}')" 422 "move bad direction -> 422"
ok "$(code -X POST $B/queue/$C/move -H 'content-type: application/json' -d '{}')" 422 "move empty body -> 422"
ok "$(code -X POST $B/queue/$C/move -H 'content-type: application/json' -d '{"position":0}')" 422 "move position 0 -> 422"
ok "$(code -X POST $B/queue/nope/move -H 'content-type: application/json' -d '{"position":1}')" 404 "move unknown job -> 404"
# --- pause / remove
ok "$(curl -s -X POST $B/queue/$C/pause | jq -c '[.status,.queue_position]')" '["paused",3]' "pause C -> paused, moved to end (3)"
ok "$(order)" "B,D,C(p)" "order B,D,C(paused)"
ok "$(curl -s -X POST $B/queue/$C/pause | jq -c '[.status,.queue_position]')" '["paused",3]' "pause again is idempotent"
ok "$(code -X POST $B/queue/$A/pause)" 409 "pause running job -> 409"
ok "$(code -X POST $B/jobs/$Bj/stop)" 409 "stop queued job -> 409"
ok "$(curl -s -X DELETE $B/queue/$D | jq -r .status)" cancelled "DELETE /queue/D -> cancelled"
ok "$(order)" "B,C(p)" "order B,C(paused)"
ok "$(code -X DELETE $B/queue/$D)" 409 "DELETE cancelled job again -> 409"
ok "$(curl -s "$B/jobs?status=cancelled" | jq -r --arg d $D '[.[]|select(.job_id==$d)]|length')" 1 "cancelled D kept in history"
# --- scheduler: A done -> B starts (C paused is skipped)
for i in $(seq 60); do [ "$(st $A)" != running ] && break; sleep 0.5; done
ok "$(st $A)" done "A finished"
sleep 1
ok "$(st $Bj)" running "B started after A"
ok "$(order)" "C(p)" "paused C skipped, still queued as paused"
ok "$(curl -s $B/jobs/$A | jq -r '[.log[].msg]|map(select(test("step delay 1s, timeout 300s")))|length')" 1 "run used current settings (delay 1s, timeout 300s)"
ok "$(curl -s -X POST $B/queue/$C/unpause | jq -c '[.status,.queue_position]')" '["queued",1]' "unpause C -> queued, keeps position"
for i in $(seq 60); do [ "$(st $Bj)" != running ] && break; sleep 0.5; done
sleep 1
ok "$(st $C)" running "C started after B"
for i in $(seq 60); do [ "$(st $C)" != running ] && break; sleep 0.5; done
ok "$(st $C)" done "C finished"
ok "$(curl -s "$B/jobs?limit=200" | jq -r --arg a $A --arg b $Bj --arg c $C '[.[]|select(.job_id==$a or .job_id==$b or .job_id==$c)]|sort_by(.started_at)|map(.title)|join(",")')" "q_A,q_B,q_C" "started in order A,B,C"
# --- resume / restart enqueue while busy
E=$(mk E stub-slow | jq -r .job_id); IDS+=($E)
for i in $(seq 40); do [ "$(curl -s $B/jobs/$E | jq -r '.steps[0].status')" = running ] && break; sleep 0.2; done
ok "$(curl -s -X POST $B/jobs/$E/stop | jq -r .status)" stopped "stop E"
F=$(mk F stub-slow | jq -r .job_id); IDS+=($F)
ok "$(curl -s -X POST $B/jobs/$E/resume | jq -c '[.status,.queue_position,.resumed_from_step]')" '["queued",1,1]' "resume E while F runs -> queued"
ok "$(code -X POST $B/jobs/$E/resume)" 409 "second resume of queued E -> 409"
R=$(curl -s -X POST $B/jobs/$Bj/restart); ok "$(jq -c '[.status,.queue_position]' <<<"$R")" '["queued",2]' "restart B while busy -> queued 2"
IDS+=($(jq -r .job_id <<<"$R"))
ok "$(code -X POST $B/jobs/$Bj/restart)" 409 "second restart of B while first is queued -> 409"
ok "$(curl -s $B/queue | jq -r '.count')" 2 "queue count 2"
# cleanup: remove queued, stop running
for j in $(curl -s $B/queue | jq -r '.queued[].job_id'); do curl -s -X DELETE $B/queue/$j >/dev/null; done
curl -s -X POST $B/jobs/$F/stop >/dev/null
sleep 1
ok "$(curl -s $B/queue | jq -c '[.running,.count]')" '[null,0]' "queue empty after cleanup"
curl -s -X POST $B/settings/reset >/dev/null
echo "IDS ${IDS[*]}"
if command -v mongosh >/dev/null 2>&1; then
  ENVF="$(dirname "$(readlink -f "$0")")/../.env"
  MURL=$(grep '^MONGO_URL=' "$ENVF" | cut -d= -f2-); DBN=$(grep '^DB_NAME=' "$ENVF" | cut -d= -f2-)
  JS="const ids=$(printf '%s\n' "${IDS[@]}" | jq -R . | jq -sc .); const all=ids.concat(db.jobs.find({restarted_from:{\$in:ids}},{_id:0,id:1}).toArray().map(j=>j.id));"
  JS="$JS print('cleanup: deleted', db.jobs.deleteMany({id:{\$in:all}}).deletedCount, 'jobs', db.steps.deleteMany({job_id:{\$in:all}}).deletedCount, 'steps')"
  mongosh --quiet "${MURL%/}/$DBN" --eval "$JS"
fi
echo "$PASS passed, $FAIL failed"
