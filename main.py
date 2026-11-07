from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Ticket, TicketStatus
from schemas import TicketCreate, TicketResponse
from tasks import process_ticket_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Tickets API",
    version="1.2.0",
    lifespan=lifespan,
)


@app.post(
    "/tickets",
    response_model=TicketResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Tickets"],
)
def create_ticket(
    ticket_in: TicketCreate,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> Ticket:
    cleaned_key = idempotency_key.strip()
    if not cleaned_key:
        raise HTTPException(
            status_code=422,
            detail="Header 'Idempotency-Key' cannot be empty or whitespace only.",
        )

    new_ticket = Ticket(
        text=ticket_in.text,
        idempotency_key=cleaned_key,
        status=TicketStatus.PENDING.value,
    )
    try:
        db.add(new_ticket)
        db.commit()
        db.refresh(new_ticket)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Duplicate Idempotency-Key '{cleaned_key}'.",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist ticket.",
        ) from exc

    background_tasks.add_task(process_ticket_task, new_ticket.id)
    return new_ticket


@app.get(
    "/tickets",
    response_model=list[TicketResponse],
    status_code=status.HTTP_200_OK,
    tags=["Tickets"],
)
def list_tickets(
    db: Annotated[Session, Depends(get_db)],
    ticket_status: Annotated[TicketStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[Ticket]:
    stmt = select(Ticket).order_by(Ticket.created_at.desc()).limit(limit)
    if ticket_status is not None:
        stmt = stmt.where(Ticket.status == ticket_status.value)
    return list(db.scalars(stmt).all())


@app.get(
    "/tickets/{ticket_id}",
    response_model=TicketResponse,
    status_code=status.HTTP_200_OK,
    tags=["Tickets"],
)
def get_ticket(
    ticket_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {ticket_id} not found.",
        )
    return ticket
