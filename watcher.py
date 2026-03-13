"""File watcher daemon for the semantic index.

Monitors configured directories using watchdog and automatically indexes
new/changed files and removes deleted files from LanceDB.

Uses a 5-second debounce to avoid indexing files during active saves.
Respects exclude patterns and supported file types from config.

Usage:
    python watcher.py
"""

import fnmatch
import importlib.util
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

# Add parent dir to path for lib imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config_loader import get_semantic_config
from lib.db import get_db, get_image_table, get_text_table

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("watcher")

# ---------------------------------------------------------------------------
# File-type helpers (mirrors pipeline_wrapper.py constants)
# ---------------------------------------------------------------------------
TEXT_EXTENSIONS = {".pdf", ".md", ".docx", ".ods"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tiff"}
ALL_SUPPORTED = TEXT_EXTENSIONS | IMAGE_EXTENSIONS


def _load_indexer():
    """Lazily load the PipelineWrapper from index_path pipeline.

    Returns a configured PipelineWrapper instance with setup() already called.
    """
    wrapper_path = (
        Path(__file__).resolve().parent
        / "pipelines"
        / "index_path"
        / "pipeline_wrapper.py"
    )
    spec = importlib.util.spec_from_file_location(
        "index_path_wrapper", wrapper_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    indexer = mod.PipelineWrapper()
    indexer.setup()
    return indexer


def _is_supported(file_path: str, config: dict) -> str | None:
    """Return 'text' or 'image' if the file extension is supported, else None."""
    ext = Path(file_path).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return None


def _is_excluded(file_path: str, exclude_patterns: list[str]) -> bool:
    """Return True if file_path matches any exclude pattern."""
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# ChangeHandler
# ---------------------------------------------------------------------------
class ChangeHandler(FileSystemEventHandler):
    """Watchdog event handler with debounce and LanceDB integration.

    On file create/modify: schedules indexing after a debounce period.
    On file delete: immediately removes entries from LanceDB.
    """

    def __init__(
        self,
        config: dict,
        text_table,
        image_table,
        indexer,
        debounce_seconds: float = 5.0,
    ) -> None:
        super().__init__()
        self.config = config
        self.text_table = text_table
        self.image_table = image_table
        self.indexer = indexer
        self.debounce_seconds = debounce_seconds
        self.exclude_patterns: list[str] = config.get("exclude", [])

        # Debounce state: {filepath_str: threading.Timer}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _should_skip(self, path: str) -> bool:
        """Return True if the file should not be processed."""
        if _is_excluded(path, self.exclude_patterns):
            return True
        if _is_supported(path, self.config) is None:
            return True
        return False

    # -- Event handlers -------------------------------------------------------

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule_index(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule_index(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        file_path = str(event.src_path)
        if self._should_skip(file_path):
            return

        # Cancel any pending index timer for this file
        with self._lock:
            timer = self._timers.pop(file_path, None)
            if timer is not None:
                timer.cancel()

        self._remove_from_db(file_path)

    # -- Debounce logic -------------------------------------------------------

    def _schedule_index(self, file_path: str) -> None:
        """Schedule indexing of a file after the debounce period.

        If the file changes again before the timer fires, the previous
        timer is cancelled and a new one is started.
        """
        if self._should_skip(file_path):
            return

        with self._lock:
            # Cancel existing timer for this file
            existing = self._timers.get(file_path)
            if existing is not None:
                existing.cancel()

            # Start new debounce timer
            timer = threading.Timer(
                self.debounce_seconds,
                self._do_index,
                args=(file_path,),
            )
            timer.daemon = True
            timer.name = f"debounce-{Path(file_path).name}"
            self._timers[file_path] = timer
            timer.start()

        logger.debug(
            "Scheduled indexing for %s (debounce %.1fs)",
            file_path,
            self.debounce_seconds,
        )

    def _do_index(self, file_path: str) -> None:
        """Index a single file. Called by the debounce timer."""
        # Clean up timer reference
        with self._lock:
            self._timers.pop(file_path, None)

        # Verify file still exists (could have been deleted during debounce)
        if not Path(file_path).exists():
            logger.info("File disappeared before indexing: %s", file_path)
            return

        logger.info("Indexing file: %s", file_path)

        try:
            # Remove old entries first (re-index = delete + add)
            self._remove_from_db(file_path)

            # Index the file
            result = self.indexer.run_api(path=file_path, recursive=False)
            indexed = result.get("indexed_count", 0)
            errors = result.get("errors", [])

            if errors:
                for err in errors:
                    logger.error("Indexing error: %s", err)
            else:
                logger.info(
                    "Indexed %s: %d chunks/records", file_path, indexed
                )
        except Exception:
            logger.exception("Failed to index %s", file_path)

    # -- LanceDB deletion ----------------------------------------------------

    def _remove_from_db(self, file_path: str) -> None:
        """Remove all entries for a file from both LanceDB tables."""
        # Escape single quotes in file path for SQL filter
        safe_path = file_path.replace("'", "''")

        try:
            self.text_table.delete(f"file_path = '{safe_path}'")
            logger.debug("Removed text entries for %s", file_path)
        except Exception:
            logger.debug(
                "No text entries to remove for %s (or table empty)", file_path
            )

        try:
            self.image_table.delete(f"file_path = '{safe_path}'")
            logger.debug("Removed image entries for %s", file_path)
        except Exception:
            logger.debug(
                "No image entries to remove for %s (or table empty)",
                file_path,
            )

    # -- Cleanup --------------------------------------------------------------

    def cancel_all_timers(self) -> None:
        """Cancel all pending debounce timers (for clean shutdown)."""
        with self._lock:
            for path, timer in self._timers.items():
                timer.cancel()
                logger.debug("Cancelled pending timer for %s", path)
            self._timers.clear()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the file watcher daemon."""
    logger.info("Starting semantic index watcher")

    # Load configuration
    config = get_semantic_config()
    watch_paths = config.get("watch_paths", [])
    watcher_cfg = config.get("watcher", {})
    debounce_seconds = watcher_cfg.get("debounce_seconds", 5)

    if not watch_paths:
        logger.error("No watch_paths configured. Exiting.")
        sys.exit(1)

    # Connect to LanceDB
    logger.info("Connecting to LanceDB")
    db = get_db()
    text_table = get_text_table(db)
    image_table = get_image_table(db)

    # Load the indexing pipeline
    logger.info("Loading indexing pipeline (this may take a moment)...")
    indexer = _load_indexer()
    logger.info("Indexing pipeline ready")

    # Create handler and observers
    handler = ChangeHandler(
        config=config,
        text_table=text_table,
        image_table=image_table,
        indexer=indexer,
        debounce_seconds=debounce_seconds,
    )

    observers: list[Observer] = []
    for watch_path in watch_paths:
        path = Path(watch_path)
        if not path.exists():
            logger.warning("Watch path does not exist, skipping: %s", path)
            continue

        observer = Observer()
        observer.schedule(handler, str(path), recursive=True)
        observer.daemon = True
        observers.append(observer)
        logger.info("Watching: %s (recursive)", path)

    if not observers:
        logger.error("No valid watch paths found. Exiting.")
        sys.exit(1)

    # Start all observers
    for observer in observers:
        observer.start()

    logger.info(
        "Watcher running (%d paths, %.0fs debounce). Press Ctrl+C to stop.",
        len(observers),
        debounce_seconds,
    )

    # Graceful shutdown handler
    shutdown_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Main loop: wait for shutdown signal
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        pass

    # Clean shutdown
    logger.info("Stopping watcher...")
    handler.cancel_all_timers()
    for observer in observers:
        observer.stop()
    for observer in observers:
        observer.join(timeout=5.0)

    logger.info("Watcher stopped.")


if __name__ == "__main__":
    main()
