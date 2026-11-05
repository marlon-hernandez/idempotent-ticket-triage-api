from datetime import datetime, timezone
import logging
import os

import requests
from sqlalchemy.exc import SQLAlchemyError

from database import SessionLocal
from models import Ticket, TicketStatus

logger = logging.getLogger(__name__)

OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "30"))


def classify_ticket_with_ollama(ticket_text: str) -> str:
    prompt = (
        "You are a support ticket triage system. "
        "Classify the following support ticket into one primary category "
        "(BILLING, TECHNICAL_ISSUE, ACCOUNT_ACCESS, FEATURE_REQUEST, or GENERAL) "
        "and assign a priority level (LOW, MEDIUM, HIGH, CRITICAL). "
        "Respond strictly in the format: CATEGORY | PRIORITY | One-sentence summary.\n\n"
        f"Ticket:\n{ticket_text}"
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    response = requests.post(OLLAMA_API_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    result = data.get("response", "").strip()
    if not result:
        raise ValueError("Empty response received from Ollama.")

    return result


def process_ticket_task(ticket_id: int) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            logger.warning("Ticket id=%s not found.", ticket_id)
            return

        try:
            ticket.status = TicketStatus.PROCESSING.value
            db.commit()

            classification_result = classify_ticket_with_ollama(ticket.text)

            ticket.classification = classification_result
            ticket.status = TicketStatus.COMPLETED.value
            ticket.processed_at = datetime.now(timezone.utc)
            db.commit()

        except (requests.RequestException, ValueError) as exc:
            db.rollback()
            logger.error("Ollama classification failed for ticket id=%s: %s", ticket_id, exc)
            try:
                ticket.status = TicketStatus.FAILED.value
                ticket.classification = f"Classification error: {exc}"
                db.commit()
            except SQLAlchemyError:
                db.rollback()
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Database error while updating ticket id=%s.", ticket_id)
