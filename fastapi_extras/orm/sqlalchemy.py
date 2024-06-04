from typing import Any, Generator, Union

from pydantic import AnyUrl
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(
        self,
        database_url: Union[str, AnyUrl],
        autocommit: bool = False,
        autoflush: bool = False,
        **kwargs: Any,
    ) -> None:
        self.engine = create_engine(str(database_url), **kwargs)
        self.sessionmaker = sessionmaker(
            autocommit=autocommit, autoflush=autoflush, bind=self.engine
        )

    def __call__(self) -> Generator[Session, None, None]:
        session = self.sessionmaker()

        try:
            yield session
        finally:
            session.close()
