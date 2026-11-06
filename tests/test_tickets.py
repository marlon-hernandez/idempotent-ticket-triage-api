from pathlib import Path
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import Base, get_db
from main import app
import tasks

TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_database(monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(tasks, "SessionLocal", TestingSessionLocal)
    yield
    Base.metadata.drop_all(bind=test_engine)


client = TestClient(app)


@patch(
    "tasks.classify_ticket_with_ollama",
    return_value="TECHNICAL_ISSUE | HIGH | User encounters 500 error during login.",
)
def test_create_ticket_and_background_classification(mock_ollama):
    response = client.post(
        "/tickets",
        headers={"Idempotency-Key": "key-1001"},
        json={"text": "User encounters a 500 internal error when logging in."},
    )
    assert response.status_code == 202
    data = response.json()
    ticket_id = data["id"]
    assert data["idempotency_key"] == "key-1001"

    get_response = client.get(f"/tickets/{ticket_id}")
    assert get_response.status_code == 200
    updated = get_response.json()
    assert updated["status"] == "COMPLETED"
    assert updated["classification"] == "TECHNICAL_ISSUE | HIGH | User encounters 500 error during login."
    assert updated["processed_at"] is not None
    mock_ollama.assert_called_once()


@patch(
    "tasks.classify_ticket_with_ollama",
    return_value="BILLING | MEDIUM | Duplicate charge reported.",
)
def test_duplicate_idempotency_key_rejected(mock_ollama):
    headers = {"Idempotency-Key": "dup-key-999"}
    payload = {"text": "I was charged twice for my monthly subscription."}

    first = client.post("/tickets", headers=headers, json=payload)
    assert first.status_code == 202

    second = client.post("/tickets", headers=headers, json=payload)
    assert second.status_code == 409
    assert "dup-key-999" in second.json()["detail"]


def test_missing_or_blank_idempotency_key_returns_422():
    missing = client.post(
        "/tickets",
        json={"text": "Cannot reset my account password."},
    )
    assert missing.status_code == 422

    blank = client.post(
        "/tickets",
        headers={"Idempotency-Key": "   "},
        json={"text": "Cannot reset my account password."},
    )
    assert blank.status_code == 422


@patch(
    "tasks.classify_ticket_with_ollama",
    side_effect=requests.Timeout("Ollama request timed out after 30s"),
)
def test_ollama_timeout_marks_ticket_failed(mock_ollama):
    response = client.post(
        "/tickets",
        headers={"Idempotency-Key": "timeout-key-01"},
        json={"text": "VPN client drops connection every 10 minutes."},
    )
    assert response.status_code == 202
    ticket_id = response.json()["id"]

    fetched = client.get(f"/tickets/{ticket_id}").json()
    assert fetched["status"] == "FAILED"
    assert "timed out" in fetched["classification"]


@patch(
    "tasks.classify_ticket_with_ollama",
