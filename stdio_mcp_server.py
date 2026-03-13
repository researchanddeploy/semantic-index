"""Stdio MCP server wrapping Hayhooks semantic search pipelines.

Exposes two tools over JSON-RPC stdio transport:
  - semantic_search: vector similarity search over indexed documents
  - index_path: index files/directories into the semantic database

Replaces the SSE-based Hayhooks MCP server to avoid the Starlette 0.52.1
notification crash.
"""

import json
import logging
import sys
import traceback

from mcp.server import FastMCP

# ---------------------------------------------------------------------------
# Logging — write to stderr so stdout stays clean for JSON-RPC
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("stdio_mcp_server")

# ---------------------------------------------------------------------------
# Lazy pipeline singletons
# ---------------------------------------------------------------------------
_search_pipeline = None
_index_pipeline = None


def _get_search_pipeline():
    global _search_pipeline
    if _search_pipeline is None:
        logger.info("Initializing semantic_search pipeline...")
        from pipelines.semantic_search.pipeline_wrapper import PipelineWrapper

        _search_pipeline = PipelineWrapper()
        _search_pipeline.setup()
        logger.info("semantic_search pipeline ready.")
    return _search_pipeline


def _get_index_pipeline():
    global _index_pipeline
    if _index_pipeline is None:
        logger.info("Initializing index_path pipeline...")
        from pipelines.index_path.pipeline_wrapper import PipelineWrapper

        _index_pipeline = PipelineWrapper()
        _index_pipeline.setup()
        logger.info("index_path pipeline ready.")
    return _index_pipeline


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="semantic-index",
    instructions=(
        "Semantic search and indexing over local documents. "
        "Use semantic_search to find relevant files by meaning. "
        "Use index_path to add new files to the search index."
    ),
)


@mcp.tool()
def semantic_search(
    query: str,
    top_k: int = 10,
    file_types: list[str] | None = None,
) -> str:
    """Search indexed documents by semantic similarity.

    Embeds the query and searches LanceDB for the most similar text chunks
    and images. Text uses all-MiniLM-L6-v2, images use CLIP.

    Args:
        query: Natural language search query.
        top_k: Maximum results to return (default 10).
        file_types: Optional filter, e.g. ["pdf", "md"] or ["jpg", "png"].
                    If omitted, searches both text and images.

    Returns:
        JSON string with a "results" list of {path, score, snippet, file_type}.
    """
    try:
        pipeline = _get_search_pipeline()
        result = pipeline.run_api(query=query, top_k=top_k, file_types=file_types)
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        error_msg = traceback.format_exc()
        logger.error("semantic_search failed:\n%s", error_msg)
        return json.dumps({"error": error_msg})


@mcp.tool()
def index_path(
    path: str,
    recursive: bool = True,
) -> str:
    """Index files from a path into the semantic search database.

    Walks the given path, converts supported documents (PDF, Markdown, DOCX,
    ODS), splits text into chunks, embeds, and stores in LanceDB. Images
    (JPG, PNG, WebP, TIFF) are embedded with CLIP.

    Args:
        path: File or directory path to index.
        recursive: Whether to walk subdirectories (default True).

    Returns:
        JSON string with indexed_count, skipped_count, and errors list.
    """
    try:
        pipeline = _get_index_pipeline()
        result = pipeline.run_api(path=path, recursive=recursive)
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        error_msg = traceback.format_exc()
        logger.error("index_path failed:\n%s", error_msg)
        return json.dumps({"error": error_msg})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting semantic-index stdio MCP server...")
    mcp.run(transport="stdio")
