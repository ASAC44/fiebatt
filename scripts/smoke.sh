#!/usr/bin/env bash
# end-to-end smoke test: upload -> generate -> accept -> timeline -> export
# requires: a running backend on $BASE, a test video at $VIDEO, jq installed
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
VIDEO="${VIDEO:-apps/api/tests/fixtures/test_5s.mp4}"
SID="${SID:-$(uuidgen)}"

echo "session: $SID"
echo "video:   $VIDEO"
echo "base:    $BASE"

echo
echo "==> health"
curl -fs "$BASE/api/health" | jq

echo
echo "==> upload"
UPLOAD=$(curl -fs -H "X-Session-Id: $SID" -F "file=@$VIDEO" "$BASE/api/upload")
echo "$UPLOAD" | jq
PROJECT=$(echo "$UPLOAD" | jq -r .project_id)
DUR=$(echo "$UPLOAD" | jq -r .duration)

echo
echo "==> generate"
START=1.0
END=3.0
JOB=$(curl -fs -H "X-Session-Id: $SID" -H 'Content-Type: application/json' \
  -d "{\"project_id\":\"$PROJECT\",\"start_ts\":$START,\"end_ts\":$END,\"bbox\":{\"x\":0.25,\"y\":0.4,\"w\":0.3,\"h\":0.35},\"prompt\":\"make it red\",\"reference_frame_ts\":2.0}" \
  "$BASE/api/generate" | jq -r .job_id)
echo "job_id: $JOB"

echo
echo "==> poll"
while :; do
  STATUS=$(curl -fs -H "X-Session-Id: $SID" "$BASE/api/jobs/$JOB" | jq -r .status)
  echo "  status=$STATUS"
  [ "$STATUS" = "done" ] && break
  [ "$STATUS" = "error" ] && { curl -s -H "X-Session-Id: $SID" "$BASE/api/jobs/$JOB" | jq; exit 1; }
  sleep 1
done

echo
echo "==> accept variant 0"
ACCEPT=$(curl -fs -H "X-Session-Id: $SID" -H 'Content-Type: application/json' \
  -d "{\"job_id\":\"$JOB\",\"variant_index\":0}" "$BASE/api/accept")
echo "$ACCEPT" | jq

echo
echo "==> timeline"
curl -fs -H "X-Session-Id: $SID" "$BASE/api/timeline/$PROJECT" | jq

echo
echo "==> export"
EXPORT=$(curl -fs -H "X-Session-Id: $SID" -H 'Content-Type: application/json' \
  -d "{\"project_id\":\"$PROJECT\"}" "$BASE/api/export" | jq -r .export_job_id)
while :; do
  STATUS=$(curl -fs -H "X-Session-Id: $SID" "$BASE/api/export/$EXPORT" | jq -r .status)
  echo "  export status=$STATUS"
  [ "$STATUS" = "done" ] && break
  [ "$STATUS" = "error" ] && exit 1
  sleep 1
done
curl -fs -H "X-Session-Id: $SID" "$BASE/api/export/$EXPORT" | jq
