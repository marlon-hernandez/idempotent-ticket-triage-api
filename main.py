from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Ticket
from schemas import TicketCreate, TicketResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Tickets API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post(
    "/tickets",
    response_model=TicketResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Tickets"],
)
def create_ticket(
    ticket_in: TicketCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Ticket:
    new_ticket = Ticket(text=ticket_in.text)
    try:
        db.add(new_ticket)
        db.commit()
        db.refresh(new_ticket)
        return new_ticket
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist ticket.",
        ) from exc


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
