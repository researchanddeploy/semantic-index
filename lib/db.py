"""Shared LanceDB connection and table management.

Provides functions to connect to the LanceDB instance and get/create
tables for text documents and image documents.
"""

import logging
from typing import Any

import lancedb
import pyarrow as pa

from lib.config_loader import get_semantic_config

logger = logging.getLogger(__name__)

# Table names
TEXT_TABLE = "text_documents"
IMAGE_TABLE = "image_documents"


def get_db(db_path: str | None = None) -> lancedb.DBConnection:
    """Connect to the LanceDB database.

    Args:
        db_path: Optional override for the database path.
                 Defaults to config's lancedb_dir.

    Returns:
        LanceDB connection object.
    """
    if db_path is None:
        config = get_semantic_config()
        db_path = config["lancedb_dir"]

    return lancedb.connect(db_path)


def _text_schema(dim: int = 384) -> pa.Schema:
    """PyArrow schema for the text_documents table."""
    return pa.schema([
        pa.field("vector", pa.list_(pa.float32(), dim)),
        pa.field("content", pa.utf8()),
        pa.field("file_path", pa.utf8()),
        pa.field("file_type", pa.utf8()),
        pa.field("chunk_index", pa.int32()),
    ])


def _image_schema(dim: int = 512) -> pa.Schema:
    """PyArrow schema for the image_documents table."""
    return pa.schema([
        pa.field("vector", pa.list_(pa.float32(), dim)),
        pa.field("file_path", pa.utf8()),
        pa.field("file_type", pa.utf8()),
    ])


def get_text_table(
    db: lancedb.DBConnection | None = None,
    dim: int = 384,
) -> lancedb.table.LanceTable:
    """Get or create the text_documents table.

    Args:
        db: Optional existing DB connection. Will create one if None.
        dim: Embedding vector dimension (default 384 for all-MiniLM-L6-v2).

    Returns:
        LanceDB table for text documents.
    """
    if db is None:
        db = get_db()

    existing = db.table_names()
    if hasattr(existing, "tables"):
        # Newer API returns a PageToken object
        existing = existing.tables
    existing = list(existing)

    if TEXT_TABLE in existing:
        return db.open_table(TEXT_TABLE)

    logger.info("Creating text_documents table with dim=%d", dim)
    return db.create_table(TEXT_TABLE, schema=_text_schema(dim))


def get_image_table(
    db: lancedb.DBConnection | None = None,
    dim: int = 512,
) -> lancedb.table.LanceTable:
    """Get or create the image_documents table.

    Args:
        db: Optional existing DB connection. Will create one if None.
        dim: Embedding vector dimension (default 512 for clip-ViT-B-32).

    Returns:
        LanceDB table for image documents.
    """
    if db is None:
        db = get_db()

    existing = db.table_names()
    if hasattr(existing, "tables"):
        existing = existing.tables
    existing = list(existing)

    if IMAGE_TABLE in existing:
        return db.open_table(IMAGE_TABLE)

    logger.info("Creating image_documents table with dim=%d", dim)
    return db.create_table(IMAGE_TABLE, schema=_image_schema(dim))
