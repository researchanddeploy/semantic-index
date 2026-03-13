"""Indexing PipelineWrapper for Hayhooks.

Walks a file or directory path, converts documents by file type,
embeds them, and stores them in LanceDB.
"""

import fnmatch
import logging
import os
from pathlib import Path

from hayhooks import BasePipelineWrapper
from haystack import Document
from haystack.components.converters import (
    DOCXToDocument,
    MarkdownToDocument,
    PyPDFToDocument,
)
from haystack.components.preprocessors import DocumentSplitter
from PIL import Image
from sentence_transformers import SentenceTransformer

from lib.config_loader import get_semantic_config
from lib.db import get_db, get_image_table, get_text_table
from lib.ods_converter import ODSToDocument

logger = logging.getLogger(__name__)

# Extension routing
TEXT_CONVERTERS = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".docx": "docx",
    ".ods": "ods",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tiff"}


class PipelineWrapper(BasePipelineWrapper):
    """Index files from a path into the semantic search database.

    Walks a file or directory, converts supported document types,
    splits and embeds text, embeds images with CLIP, and stores
    everything in LanceDB for later semantic search.
    """

    skip_mcp = False

    def setup(self) -> None:
        """Initialize converters, embedders, splitter, and LanceDB tables."""
        self.config = get_semantic_config()

        # Text converters
        self.converters = {
            "pdf": PyPDFToDocument(),
            "markdown": MarkdownToDocument(),
            "docx": DOCXToDocument(),
            "ods": ODSToDocument(),
        }

        # Document splitter
        splitter_cfg = self.config.get("splitter", {})
        self.splitter = DocumentSplitter(
            split_by=splitter_cfg.get("split_by", "sentence"),
            split_length=splitter_cfg.get("split_length", 3),
            split_overlap=splitter_cfg.get("split_overlap", 1),
        )

        # Embedding models
        text_model = self.config.get("models", {}).get(
            "text_embedder", "sentence-transformers/all-MiniLM-L6-v2"
        )
        image_model = self.config.get("models", {}).get(
            "image_embedder", "sentence-transformers/clip-ViT-B-32"
        )

        logger.info("Loading text embedder: %s", text_model)
        self.text_embedder = SentenceTransformer(text_model)

        logger.info("Loading image embedder: %s", image_model)
        self.image_embedder = SentenceTransformer(image_model)

        # LanceDB tables
        self.db = get_db()
        text_dim = self.config.get("text_embedding_dim", 384)
        image_dim = self.config.get("image_embedding_dim", 512)
        self.text_table = get_text_table(self.db, dim=text_dim)
        self.image_table = get_image_table(self.db, dim=image_dim)

        # Exclude patterns
        self.exclude_patterns = self.config.get("exclude", [])

        # Warm up splitter (Haystack 2.x components may need warm_up)
        for converter in self.converters.values():
            if hasattr(converter, "warm_up"):
                converter.warm_up()
        if hasattr(self.splitter, "warm_up"):
            self.splitter.warm_up()

    def _is_excluded(self, file_path: str) -> bool:
        """Check if a file path matches any exclude pattern."""
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(file_path, pattern):
                return True
        return False

    def _collect_files(self, path: str, recursive: bool) -> list[Path]:
        """Collect all supported files from a path."""
        target = Path(path)
        files: list[Path] = []

        if target.is_file():
            files.append(target)
        elif target.is_dir():
            if recursive:
                for root, _dirs, filenames in os.walk(target):
                    for fname in filenames:
                        files.append(Path(root) / fname)
            else:
                for item in target.iterdir():
                    if item.is_file():
                        files.append(item)

        return files

    def _get_file_type(self, file_path: Path) -> str | None:
        """Determine the file type category (text converter key or 'image')."""
        ext = file_path.suffix.lower()
        if ext in TEXT_CONVERTERS:
            return TEXT_CONVERTERS[ext]
        if ext in IMAGE_EXTENSIONS:
            return "image"
        return None

    def _pdf_fallback(self, file_path: Path) -> list[Document]:
        """Extract text from PDF using pymupdf as fallback."""
        import pymupdf

        docs = []
        with pymupdf.open(str(file_path)) as pdf:
            for page in pdf:
                text = page.get_text().strip()
                if text:
                    docs.append(Document(
                        content=text,
                        meta={"file_path": str(file_path), "page": page.number + 1},
                    ))
        return docs

    def _index_text_file(
        self, file_path: Path, converter_key: str
    ) -> int:
        """Convert, split, embed, and store a text file. Returns chunk count."""
        converter = self.converters[converter_key]

        # Convert file to documents
        documents: list[Document] = []
        if converter_key == "pdf":
            # PDF: try PyPDF first, fall back to pymupdf on any error
            try:
                result = converter.run(sources=[file_path])
                documents = result.get("documents", [])
                # PyPDF sometimes returns empty docs without raising
                documents = [d for d in documents if d.content and d.content.strip()]
            except Exception as exc:
                logger.info("PyPDF failed for %s (%s), trying pymupdf", file_path, exc)
            if not documents:
                try:
                    documents = self._pdf_fallback(file_path)
                except Exception as exc2:
                    logger.warning("pymupdf also failed for %s: %s", file_path, exc2)
        else:
            result = converter.run(sources=[file_path])
            documents = result.get("documents", [])

        if not documents:
            logger.warning("No documents extracted from %s", file_path)
            return 0

        # Split into chunks
        split_result = self.splitter.run(documents=documents)
        chunks: list[Document] = split_result.get("documents", [])

        if not chunks:
            return 0

        # Extract text content for embedding
        texts = [chunk.content or "" for chunk in chunks]

        # Embed all chunks in one batch
        embeddings = self.text_embedder.encode(texts, show_progress_bar=False)

        # Prepare records for LanceDB
        ext = file_path.suffix.lower().lstrip(".")
        records = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            records.append({
                "vector": embedding.tolist(),
                "content": chunk.content or "",
                "file_path": str(file_path),
                "file_type": ext,
                "chunk_index": i,
            })

        # Store in LanceDB
        if records:
            self.text_table.add(records)

        return len(records)

    def _index_image_file(self, file_path: Path) -> int:
        """Embed and store an image file. Returns 1 on success, 0 on failure."""
        # Load image with PIL
        image = Image.open(file_path).convert("RGB")

        # Skip degenerate images (thin lines, icons) — CLIP needs >= 10px per dim
        if min(image.size) < 10:
            logger.debug("Skipping tiny image %s (%s)", file_path, image.size)
            return 0

        # Encode with CLIP
        embedding = self.image_embedder.encode(image, show_progress_bar=False)

        ext = file_path.suffix.lower().lstrip(".")
        record = {
            "vector": embedding.tolist(),
            "file_path": str(file_path),
            "file_type": ext,
        }

        self.image_table.add([record])
        return 1

    def run_api(self, path: str, recursive: bool = True) -> dict:
        """Index files from a file or directory path into the semantic database.

        Walks the given path, converts supported document types (PDF, Markdown,
        DOCX, ODS), splits text into chunks, embeds with sentence-transformers,
        and stores in LanceDB. Images (JPG, PNG, WebP, TIFF) are embedded with
        CLIP and stored separately.

        Args:
            path: File or directory path to index.
            recursive: Whether to walk subdirectories (default True).

        Returns:
            Dictionary with indexed_count, skipped_count, and errors list.
        """
        indexed_count = 0
        skipped_count = 0
        errors: list[str] = []

        files = self._collect_files(path, recursive)
        logger.info("Found %d files to process in %s", len(files), path)

        for file_path in files:
            file_str = str(file_path)

            # Check exclusions
            if self._is_excluded(file_str):
                skipped_count += 1
                continue

            file_type = self._get_file_type(file_path)
            if file_type is None:
                skipped_count += 1
                continue

            try:
                if file_type == "image":
                    count = self._index_image_file(file_path)
                else:
                    count = self._index_text_file(file_path, file_type)

                indexed_count += count
                logger.info(
                    "Indexed %s: %d chunks/records", file_path, count
                )

            except Exception as exc:
                error_msg = f"{file_path}: {exc}"
                logger.exception("Error indexing %s", file_path)
                errors.append(error_msg)

        return {
            "indexed_count": indexed_count,
            "skipped_count": skipped_count,
            "errors": errors,
        }
