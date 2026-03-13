# Semantic Index

Local semantic search over personal files -- PDFs, Markdown, DOCX, spreadsheets, and images -- powered by Haystack 2.x, LanceDB, and the Model Context Protocol (MCP).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Claude Code / Desktop                    │
│                        (MCP client)                          │
└──────────────┬──────────────────────────────┬────────────────┘
               │ semantic_search()            │ index_path()
               ▼                              ▼
┌──────────────────────────────────────────────────────────────┐
│                    Hayhooks MCP Server                        │
│              (stdio transport, auto-started)                 │
│                                                              │
│  ┌────────────────────┐    ┌─────────────────────────────┐   │
│  │  semantic_search    │    │  index_path                  │   │
│  │  PipelineWrapper    │    │  PipelineWrapper             │   │
│  │                     │    │                              │   │
│  │  all-MiniLM-L6-v2  │    │  PyPDF / pymupdf (fallback) │   │
│  │  clip-ViT-B-32     │    │  MarkdownToDocument          │   │
│  └────────┬───────────┘    │  DOCXToDocument              │   │
│           │                │  ODSToDocument (custom)       │   │
│           │                │  DocumentSplitter             │   │
│           │                │  all-MiniLM-L6-v2 (text)     │   │
│           │                │  clip-ViT-B-32 (images)      │   │
│           │                └────────────┬─────────────────┘   │
│           │                             │                     │
│           └──────────┬──────────────────┘                     │
└──────────────────────┼───────────────────────────────────────┘
                       │
                       ▼
          ┌────────────────────────┐
          │        LanceDB         │
          │   (file-based, local)  │
          │                        │
          │  text_documents        │
          │  384-dim vectors       │
          │  content, file_path,   │
          │  file_type, chunk_idx  │
          │                        │
          │  image_documents       │
          │  512-dim vectors       │
          │  file_path, file_type  │
          └────────────────────────┘
                       ▲
                       │
┌──────────────────────┼───────────────────────────────────────┐
│              File Watcher Daemon                              │
│         (watchdog + debounce timer)                           │
│                                                              │
│  Monitors configured directories for file changes.           │
│  On create/modify: debounce 5s, then index via pipeline.     │
│  On delete: immediately remove entries from LanceDB.         │
└──────────────────────────────────────────────────────────────┘
```

## Features

- **Semantic search** -- find files by meaning, not keywords, across text and images
- **Multi-modal** -- text documents embedded with all-MiniLM-L6-v2 (384-dim), images with CLIP ViT-B/32 (512-dim)
- **MCP native** -- exposes `semantic_search` and `index_path` as MCP tools via Hayhooks
- **Auto-indexing** -- watchdog daemon monitors directories and indexes new/changed files automatically
- **Smart deletion** -- when files are removed from disk, their vectors are immediately purged from the database
- **Debounced writes** -- 5-second debounce prevents indexing files mid-save
- **PDF resilience** -- dual-engine PDF extraction (PyPDF primary, pymupdf fallback)
- **Spreadsheet support** -- custom ODS converter extracts sheet content as structured text
- **Zero-server database** -- LanceDB stores vectors as local Lance files, no database server needed
- **Apple Silicon optimized** -- runs on macOS with MPS acceleration for embedding models
- **Auto-start** -- two macOS LaunchAgents keep the MCP server and watcher running across reboots

## Quick Start

### Prerequisites

- macOS on Apple Silicon (M1/M2/M3/M4)
- Python 3.11+
- The directories you want to index must be accessible (e.g., mounted volumes)

### Install

```bash
# Clone the repository
git clone https://github.com/researchanddeploy/semantic-index.git ~/.semantic-index
cd ~/.semantic-index

# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Download NLTK data (required by the sentence splitter)
python3 -c "import nltk; nltk.download('punkt_tab')"
```

### Configure

Edit `config.yaml` to set your watch paths and preferences (see [Configuration Reference](#configuration-reference) below).

### Run Manually

```bash
# Terminal 1: Start the MCP server
./start-hayhooks.sh

# Terminal 2: Start the file watcher
./start-watcher.sh
```

### Index Existing Files

The watcher only picks up new changes. To do an initial bulk index of existing files, use the MCP tool or call the pipeline directly:

```bash
source .venv/bin/activate
python3 -c "
from pipelines.index_path.pipeline_wrapper import PipelineWrapper
p = PipelineWrapper()
p.setup()
result = p.run_api(path='/path/to/your/documents', recursive=True)
print(f'Indexed: {result[\"indexed_count\"]} chunks, Errors: {len(result[\"errors\"])}')
"
```

## Configuration Reference

All settings live in `config.yaml` under the `semantic_index` key:

```yaml
semantic_index:
  base_dir: /Users/you/.semantic-index
  lancedb_dir: /Users/you/.semantic-index/lancedb

  models:
    text_embedder: sentence-transformers/all-MiniLM-L6-v2
    image_embedder: sentence-transformers/clip-ViT-B-32

  text_embedding_dim: 384
  image_embedding_dim: 512

  splitter:
    split_by: sentence      # sentence | word | passage
    split_length: 3         # number of units per chunk
    split_overlap: 1        # overlap between consecutive chunks

  watch_paths:
    - /Users/you/Documents
    - /Users/you/Notes
    - /Users/you/Pictures

  file_types:
    text:
      - "*.pdf"
      - "*.md"
      - "*.docx"
      - "*.ods"
    image:
      - "*.jpg"
      - "*.jpeg"
      - "*.png"
      - "*.webp"
      - "*.tiff"

  exclude:
    - "*/node_modules/*"
    - "*/.git/*"
    - "*/.*"
    - "*/Trash/*"
    - "*/.obsidian/*"
    - "*/.dropbox.cache/*"

  watcher:
    debounce_seconds: 5
    batch_size: 50
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `base_dir` | string | -- | Root directory of the semantic index installation |
| `lancedb_dir` | string | -- | Path to the LanceDB data directory |
| `models.text_embedder` | string | `all-MiniLM-L6-v2` | Sentence-transformers model for text embedding |
| `models.image_embedder` | string | `clip-ViT-B-32` | Sentence-transformers model for image embedding |
| `text_embedding_dim` | int | `384` | Vector dimension for text embeddings |
| `image_embedding_dim` | int | `512` | Vector dimension for image embeddings |
| `splitter.split_by` | string | `sentence` | Text splitting strategy |
| `splitter.split_length` | int | `3` | Number of units (sentences) per chunk |
| `splitter.split_overlap` | int | `1` | Overlap between consecutive chunks |
| `watch_paths` | list | `[]` | Directories the watcher daemon monitors |
| `file_types.text` | list | see above | Glob patterns for text file types |
| `file_types.image` | list | see above | Glob patterns for image file types |
| `exclude` | list | see above | Glob patterns for paths to skip |
| `watcher.debounce_seconds` | float | `5` | Seconds to wait after last file change before indexing |
| `watcher.batch_size` | int | `50` | Maximum files to process in one batch |

## MCP Integration

The semantic index exposes two MCP tools through Hayhooks:

### `semantic_search`

Search indexed documents by meaning.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | *(required)* | Natural language search query |
| `top_k` | int | `10` | Maximum number of results |
| `file_types` | list[string] | `null` | Filter by extension (e.g., `["pdf", "md"]` or `["jpg", "png"]`) |

**Returns:** A list of results, each with `path`, `score` (0-1, higher is better), `snippet` (text preview or null for images), and `file_type`.

### `index_path`

Index a file or directory into the search database.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | *(required)* | File or directory path to index |
| `recursive` | bool | `true` | Whether to walk subdirectories |

**Returns:** `indexed_count`, `skipped_count`, and `errors` list.

### Claude Code Setup

Add the semantic index as an MCP server in your Claude Code configuration (`~/.claude.json` or project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "semantic-index": {
      "command": "/Users/you/.semantic-index/start-hayhooks.sh",
      "type": "stdio"
    }
  }
}
```

### Claude Desktop Setup

In Claude Desktop settings, add a custom MCP server pointing to the start script:

```json
{
  "mcpServers": {
    "semantic-index": {
      "command": "/bin/bash",
      "args": ["/Users/you/.semantic-index/start-hayhooks.sh"]
    }
  }
}
```

Once connected, you can use natural language queries like:

> "Search my documents for information about quarterly revenue"
> "Find images related to architecture diagrams"
> "Index the files in /path/to/new/folder"

## LaunchAgent Setup (Auto-Start on Boot)

Two LaunchAgents keep the services running automatically. Install them by copying the plist files to `~/Library/LaunchAgents/`:

### Hayhooks MCP Server

Create `~/Library/LaunchAgents/com.semantic-index.hayhooks.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.semantic-index.hayhooks</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/you/.semantic-index/start-hayhooks.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/you/.semantic-index/logs/hayhooks-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/you/.semantic-index/logs/hayhooks-stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
```

### File Watcher Daemon

Create `~/Library/LaunchAgents/com.semantic-index.watcher.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.semantic-index.watcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/you/.semantic-index/start-watcher.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/you/.semantic-index/logs/watcher-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/you/.semantic-index/logs/watcher-stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
```

### Managing the Services

```bash
# Load (start) the services
launchctl load ~/Library/LaunchAgents/com.semantic-index.hayhooks.plist
launchctl load ~/Library/LaunchAgents/com.semantic-index.watcher.plist

# Unload (stop) the services
launchctl unload ~/Library/LaunchAgents/com.semantic-index.hayhooks.plist
launchctl unload ~/Library/LaunchAgents/com.semantic-index.watcher.plist

# Check status
launchctl list | grep semantic-index

# View logs
tail -f ~/.semantic-index/logs/hayhooks-stderr.log
tail -f ~/.semantic-index/logs/watcher-stderr.log
```

Both services use `KeepAlive` with `SuccessfulExit: false`, meaning launchd will restart them if they crash (but not if they exit cleanly). A 30-second `ThrottleInterval` prevents restart loops.

## File Types Supported

### Text Documents

| Extension | Converter | Notes |
|-----------|-----------|-------|
| `.pdf` | PyPDF + pymupdf fallback | Dual-engine: tries PyPDF first, falls back to pymupdf for scanned/complex PDFs |
| `.md` | MarkdownToDocument | Strips Markdown formatting, extracts plain text |
| `.docx` | DOCXToDocument | Microsoft Word Open XML format |
| `.ods` | ODSToDocument (custom) | OpenDocument Spreadsheet; each sheet becomes a document with tab-separated values |

Text files are split into chunks (default: 3 sentences with 1 sentence overlap), embedded with `all-MiniLM-L6-v2` (384 dimensions), and stored in the `text_documents` LanceDB table.

### Images

| Extension | Converter | Notes |
|-----------|-----------|-------|
| `.jpg` / `.jpeg` | PIL + CLIP | |
| `.png` | PIL + CLIP | |
| `.webp` | PIL + CLIP | |
| `.tiff` | PIL + CLIP | |

Images are opened with Pillow, converted to RGB, and embedded with `clip-ViT-B-32` (512 dimensions). Images smaller than 10x10 pixels are skipped. Each image is stored as a single record in the `image_documents` LanceDB table.

## Database Schema

### `text_documents` table

| Column | Type | Description |
|--------|------|-------------|
| `vector` | float32[384] | Text embedding from all-MiniLM-L6-v2 |
| `content` | string | Raw text content of the chunk |
| `file_path` | string | Absolute path to the source file |
| `file_type` | string | File extension without dot (e.g., `pdf`, `md`) |
| `chunk_index` | int32 | Position of this chunk within the source file |

### `image_documents` table

| Column | Type | Description |
|--------|------|-------------|
| `vector` | float32[512] | Image embedding from CLIP ViT-B/32 |
| `file_path` | string | Absolute path to the source image |
| `file_type` | string | File extension without dot (e.g., `jpg`, `png`) |

## Project Structure

```
~/.semantic-index/
├── config.yaml                          # All configuration
├── requirements.txt                     # Python dependencies
├── start-hayhooks.sh                    # MCP server launcher
├── start-watcher.sh                     # Watcher daemon launcher
├── watcher.py                           # File watcher daemon (watchdog)
├── lib/
│   ├── config_loader.py                 # YAML config reader
│   ├── db.py                            # LanceDB connection + table management
│   └── ods_converter.py                 # Custom ODS-to-Document Haystack component
├── pipelines/
│   ├── index_path/
│   │   └── pipeline_wrapper.py          # Indexing pipeline (MCP tool: index_path)
│   └── semantic_search/
│       └── pipeline_wrapper.py          # Search pipeline (MCP tool: semantic_search)
├── lancedb/                             # Vector database files (gitignored)
│   ├── text_documents.lance/
│   └── image_documents.lance/
└── logs/                                # Runtime logs (gitignored)
    ├── hayhooks-stdout.log
    ├── hayhooks-stderr.log
    ├── watcher-stdout.log
    └── watcher-stderr.log
```

## Extending

### Adding a New Text File Type

1. **Create or import a converter** that implements the Haystack `@component` interface with a `run(sources: list) -> dict[str, list[Document]]` method. See `lib/ods_converter.py` for an example.

2. **Register the converter** in `pipelines/index_path/pipeline_wrapper.py`:

```python
# Add to TEXT_CONVERTERS dict
TEXT_CONVERTERS = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".docx": "docx",
    ".ods": "ods",
    ".rst": "rst",       # new
}

# Add the converter instance in setup()
self.converters = {
    ...
    "rst": RSTToDocument(),  # new
}
```

3. **Update the watcher** -- add the extension to `TEXT_EXTENSIONS` in `watcher.py`.

4. **Update config.yaml** -- add the glob pattern to `file_types.text`.

### Changing Embedding Models

Update `config.yaml` with the new model name and dimension:

```yaml
models:
  text_embedder: BAAI/bge-small-en-v1.5
image_embedding_dim: 384    # must match the model's output dimension
```

**Important:** Changing models invalidates existing embeddings. Delete the `lancedb/` directory and re-index all files after switching models.

### Adding New Watch Paths

Simply append to the `watch_paths` list in `config.yaml` and restart the watcher:

```bash
launchctl unload ~/Library/LaunchAgents/com.semantic-index.watcher.plist
launchctl load ~/Library/LaunchAgents/com.semantic-index.watcher.plist
```

## Troubleshooting

### Services not starting

```bash
# Check if launchd loaded them
launchctl list | grep semantic-index

# Look at logs for errors
tail -50 ~/.semantic-index/logs/hayhooks-stderr.log
tail -50 ~/.semantic-index/logs/watcher-stderr.log
```

### "Watch path does not exist" warnings

The watcher skips paths that are not currently mounted. If you are indexing files on an external volume, make sure it is mounted before the watcher starts. The watcher will log a warning and continue with available paths.

### Empty search results

- Verify files have been indexed: check the LanceDB tables have data.
- Ensure the MCP server is running (`launchctl list | grep hayhooks`).
- Try a broader query or remove the `file_types` filter.

### High memory usage during bulk indexing

The embedding models load into memory on first use. Expected memory footprint:

- `all-MiniLM-L6-v2`: ~90 MB
- `clip-ViT-B-32`: ~340 MB

For large initial indexes, consider indexing directories one at a time rather than the entire volume at once.

### Re-indexing after model change

```bash
# Stop services
launchctl unload ~/Library/LaunchAgents/com.semantic-index.hayhooks.plist
launchctl unload ~/Library/LaunchAgents/com.semantic-index.watcher.plist

# Delete existing vectors
rm -rf ~/.semantic-index/lancedb/

# Restart services (tables will be recreated)
launchctl load ~/Library/LaunchAgents/com.semantic-index.hayhooks.plist
launchctl load ~/Library/LaunchAgents/com.semantic-index.watcher.plist

# Re-index your files via MCP or the manual script above
```

### Watcher not picking up changes

- Check that the file extension is in the supported list.
- Check that the path does not match an exclude pattern.
- The debounce timer waits 5 seconds after the last modification -- changes will not appear instantly.
- Hidden files and directories (starting with `.`) are excluded by default.

## License

Private repository. All rights reserved.
