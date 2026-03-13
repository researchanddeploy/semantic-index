"""Semantic Search PipelineWrapper for Hayhooks.

Provides semantic search over indexed text and image documents stored
in LanceDB, using sentence-transformers for query embedding.
"""

import logging

from hayhooks import BasePipelineWrapper
from sentence_transformers import SentenceTransformer

from lib.config_loader import get_semantic_config
from lib.db import get_db, get_image_table, get_text_table

logger = logging.getLogger(__name__)

# File extensions categorized by modality
TEXT_TYPES = {"pdf", "md", "docx", "ods"}
IMAGE_TYPES = {"jpg", "jpeg", "png", "webp", "tiff"}


class PipelineWrapper(BasePipelineWrapper):
    """Semantic search over indexed documents in the Wellness vault.

    Searches text and image documents using vector similarity.
    Text queries are embedded with all-MiniLM-L6-v2, image searches
    use CLIP embeddings. Results are merged and ranked by score.
    """

    skip_mcp = False

    def setup(self) -> None:
        """Initialize embedding models and LanceDB table connections."""
        self.config = get_semantic_config()

        # Load embedding models
        text_model = self.config.get("models", {}).get(
            "text_embedder", "sentence-transformers/all-MiniLM-L6-v2"
        )
        image_model = self.config.get("models", {}).get(
            "image_embedder", "sentence-transformers/clip-ViT-B-32"
        )

        logger.info("Loading text embedder for search: %s", text_model)
        self.text_embedder = SentenceTransformer(text_model)

        logger.info("Loading image embedder for search: %s", image_model)
        self.image_embedder = SentenceTransformer(image_model)

        # Connect to LanceDB tables
        self.db = get_db()
        text_dim = self.config.get("text_embedding_dim", 384)
        image_dim = self.config.get("image_embedding_dim", 512)
        self.text_table = get_text_table(self.db, dim=text_dim)
        self.image_table = get_image_table(self.db, dim=image_dim)

    def _classify_file_types(
        self, file_types: list[str] | None
    ) -> tuple[bool, bool]:
        """Determine whether to search text, images, or both.

        Args:
            file_types: Optional list of file extensions to filter by.

        Returns:
            Tuple of (search_text, search_images).
        """
        if file_types is None:
            return True, True

        # Normalize extensions (strip leading dots, lowercase)
        normalized = {ft.lstrip(".").lower() for ft in file_types}

        has_text = bool(normalized & TEXT_TYPES)
        has_image = bool(normalized & IMAGE_TYPES)

        # If none of the provided types match either category,
        # fall back to searching both (the filter will naturally
        # return no results for unknown types)
        if not has_text and not has_image:
            return True, True

        return has_text, has_image

    def _search_text(
        self,
        query_vector: list[float],
        top_k: int,
        file_types: list[str] | None,
    ) -> list[dict]:
        """Search the text_documents table.

        Args:
            query_vector: Embedded query vector (dim 384).
            top_k: Maximum number of results to return.
            file_types: Optional file type filter.

        Returns:
            List of result dicts with path, score, snippet, file_type.
        """
        try:
            search = self.text_table.search(query_vector).limit(top_k)
            raw_results = search.to_list()
        except Exception as exc:
            logger.warning("Text search failed: %s", exc)
            return []

        results = []
        # Normalize filter types if provided
        allowed_types = None
        if file_types is not None:
            allowed_types = {ft.lstrip(".").lower() for ft in file_types}

        for row in raw_results:
            file_type = row.get("file_type", "")

            # Apply file_type filter if specified
            if allowed_types and file_type not in allowed_types:
                continue

            distance = row.get("_distance", 0.0)
            score = 1.0 / (1.0 + distance)

            # Build a snippet from the content
            content = row.get("content", "")
            snippet = content[:300] if content else None

            results.append({
                "path": row.get("file_path", ""),
                "score": round(score, 6),
                "snippet": snippet,
                "file_type": file_type,
            })

        return results

    def _search_images(
        self,
        query_vector: list[float],
        top_k: int,
        file_types: list[str] | None,
    ) -> list[dict]:
        """Search the image_documents table.

        Args:
            query_vector: Embedded query vector (dim 512, CLIP).
            top_k: Maximum number of results to return.
            file_types: Optional file type filter.

        Returns:
            List of result dicts with path, score, snippet (None), file_type.
        """
        try:
            search = self.image_table.search(query_vector).limit(top_k)
            raw_results = search.to_list()
        except Exception as exc:
            logger.warning("Image search failed: %s", exc)
            return []

        results = []
        allowed_types = None
        if file_types is not None:
            allowed_types = {ft.lstrip(".").lower() for ft in file_types}

        for row in raw_results:
            file_type = row.get("file_type", "")

            if allowed_types and file_type not in allowed_types:
                continue

            distance = row.get("_distance", 0.0)
            score = 1.0 / (1.0 + distance)

            results.append({
                "path": row.get("file_path", ""),
                "score": round(score, 6),
                "snippet": None,
                "file_type": file_type,
            })

        return results

    def run_api(
        self,
        query: str,
        top_k: int = 10,
        file_types: list[str] | None = None,
    ) -> dict:
        """Search indexed documents by semantic similarity to a query.

        Embeds the query text and searches LanceDB for the most similar
        text chunks and images. Text documents are searched using
        all-MiniLM-L6-v2 embeddings, images using CLIP embeddings.
        Results from both modalities are merged and ranked by score.

        Args:
            query: Natural language search query.
            top_k: Maximum number of results to return (default 10).
            file_types: Optional list of file extensions to filter by
                (e.g. ["pdf", "md"] for text only, ["jpg", "png"] for
                images only). If None, searches both text and images.

        Returns:
            Dictionary with a "results" key containing a list of matches,
            each with path, score, snippet (text) or None (images),
            and file_type.
        """
        search_text, search_images = self._classify_file_types(file_types)

        all_results: list[dict] = []

        if search_text:
            logger.info("Embedding query for text search: %r", query[:100])
            text_vector = self.text_embedder.encode(
                query, show_progress_bar=False
            ).tolist()
            text_results = self._search_text(text_vector, top_k, file_types)
            all_results.extend(text_results)

        if search_images:
            logger.info("Embedding query for image search: %r", query[:100])
            image_vector = self.image_embedder.encode(
                query, show_progress_bar=False
            ).tolist()
            image_results = self._search_images(
                image_vector, top_k, file_types
            )
            all_results.extend(image_results)

        # Sort by score descending and take top_k
        all_results.sort(key=lambda r: r["score"], reverse=True)
        all_results = all_results[:top_k]

        return {"results": all_results}
