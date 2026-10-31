"""Pub/Sub Push -> Cloud Run FastAPI parser demonstration.

THIS IS A DRY-RUN MESSAGE RECEIVER, NOT A PRODUCTION PARSER.
No DB write, actual GCS fetch, idempotency table, or follow-up publishing yet.
Cloud Run verifies OIDC identity via roles/run.invoker before FastAPI executes.
"""
import base64
import json
import logging

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

app = FastAPI(title="QSearch PubSub Parser Lab")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class Event(BaseModel):
    # Mirrors schemas/qsearch-crawl-event-v1.avsc: every field required, same order.
    # strict: Avro rejects "1", 1.0 and true for an int field, so the parser does too.
    # Do not rely on the topic schema alone: a Seek replay or a schemaless topic bypasses it.
    model_config = ConfigDict(strict=True)

    event_id: str
    schema_version: int
    job_id: str
    platform: str
    event_type: str
    raw_data_uri: str


class Message(BaseModel):
    data: str
    messageId: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class PushEnvelope(BaseModel):
    message: Message
    subscription: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events/crawl-fetched", status_code=204)
def handle_fetched(payload: PushEnvelope) -> Response:
    try:
        raw = base64.b64decode(payload.message.data, validate=True)
        event = Event.model_validate_json(raw)
        if event.event_type != "crawl.fetched" or event.schema_version != 1:
            # In production: quarantine permanent schema errors and ACK only once safely stored.
            raise ValueError("Unsupported event type/schema version")
        bucket, _, obj = event.raw_data_uri.removeprefix("gs://").partition("/")
        if not event.raw_data_uri.startswith("gs://") or not bucket or not obj:
            raise ValueError("Expected gs://<bucket>/<object>")
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        log.warning("Cannot parse message: %s", exc)
        # Push acks only 102/200/201/202/204; 422 is a nack -> redelivery; after max attempts -> DLQ (best effort).
        raise HTTPException(status_code=422, detail="Invalid crawl event") from exc

    # DEMO only: log, then ack. Does not fetch or persist raw content.
    # Production: fetch GCS, parse, atomic DB upsert with UNIQUE(event_id),
    # persist parse.outbox event in same DB transaction, commit, then return 204.
    log.info(
        "Accepted event_id=%s job_id=%s platform=%s gcs=%s message_id=%s",
        event.event_id, event.job_id, event.platform,
        event.raw_data_uri, payload.message.messageId,
    )
    return Response(status_code=204)
