"""Declarative base shared by every ORM model."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
