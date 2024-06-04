from typing import List

from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, StaticPool, String, text
from sqlalchemy.orm import DeclarativeBase, Session
from typing_extensions import Annotated

from fastapi_extras.orm.sqlalchemy import Database

app = FastAPI()

db = Database("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    key = Column(String, primary_key=True, index=True)
    value = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


Base.metadata.create_all(db.engine)


class ItemSchema(BaseModel):
    key: str
    value: str

    class Config:
        from_attributes = True


@app.post("/items/", response_model=ItemSchema, status_code=status.HTTP_201_CREATED)
def create_item(item: ItemSchema, session: Annotated[Session, Depends(db)]):
    db_item = Item(**item.model_dump())
    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return db_item


@app.get("/items/", response_model=List[ItemSchema])
def read_items(session: Annotated[Session, Depends(db)]):
    return session.query(Item.key, Item.value).order_by(Item.timestamp).all()


client = TestClient(app)


def test_items():
    items = [
        {"key": "foo", "value": "bar"},
        {"key": "bar", "value": "baz"},
        {"key": "baz", "value": "foo"},
    ]

    for item in items:
        response = client.post("/items/", json=item)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == item

    response = client.get("/items/")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == items
