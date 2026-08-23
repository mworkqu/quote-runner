#!/usr/bin/env bash
# Deploy Quote Runner to Cloud Run.
#   ./deploy.sh YOUR_PROJECT_ID [REGION]
set -euo pipefail

PROJECT="${1:?usage: ./deploy.sh PROJECT_ID [REGION]}"
REGION="${2:-us-central1}"
SERVICE="quote-runner"
MODEL="${QR_MODEL:-gemini-3.5-flash}"

# Cloud Run runs the container in a real region ($REGION); the Vertex endpoint is
# separate. gemini-3.5-flash is only served on the `global` publisher endpoint —
# a regional call (e.g. us-central1) 404s — so GOOGLE_CLOUD_LOCATION must be
# global even though the service itself deploys to a region.
VERTEX_LOCATION="${QR_VERTEX_LOCATION:-global}"

gcloud config set project "$PROJECT"

gcloud services enable \
  run.googleapis.com \
  aiplatform.googleapis.com \
  cloudbuild.googleapis.com \
  cloudtrace.googleapis.com

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 600 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$VERTEX_LOCATION,QR_MODEL=$MODEL"

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
echo
echo "Deployed: $URL"
echo "Smoke test:  curl -s $URL/healthz | python3 -m json.tool"
echo "Full eval:   curl -s -X POST $URL/eval | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"n_passed\"], \"/\", d[\"n_cases\"])'"
