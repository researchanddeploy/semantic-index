"""ODS to Document converter for Haystack 2.x.

Converts OpenDocument Spreadsheet (.ods) files into Haystack Documents
using odfpy. Each sheet becomes a separate Document with tab-separated
cell values and newline-separated rows.
"""

import logging
from pathlib import Path
from typing import Union

from haystack import Document, component

from odf.opendocument import load as odf_load
from odf.table import Table, TableRow, TableCell
from odf.text import P

logger = logging.getLogger(__name__)


def _extract_cell_text(cell: TableCell) -> str:
    """Extract text content from an ODS table cell."""
    parts = []
    for p in cell.getElementsByType(P):
        # Recursively get all text content from the paragraph
        text = ""
        for node in p.childNodes:
            if hasattr(node, "data"):
                text += node.data
            elif hasattr(node, "__str__"):
                text += str(node)
        parts.append(text)
    return " ".join(parts)


def _extract_sheet_content(table: Table) -> str:
    """Extract all content from a sheet as tab-separated, newline-separated text."""
    rows = []
    for row in table.getElementsByType(TableRow):
        cells = []
        for cell in row.getElementsByType(TableCell):
            # Handle repeated cells
            repeat = cell.getAttribute("numbercolumnsrepeated")
            text = _extract_cell_text(cell)
            if repeat and text:
                repeat_count = min(int(repeat), 100)  # Cap to prevent huge expansions
                cells.extend([text] * repeat_count)
            elif text:
                cells.append(text)
            else:
                cells.append("")

        # Strip trailing empty cells
        while cells and not cells[-1]:
            cells.pop()

        if cells:
            rows.append("\t".join(cells))

    return "\n".join(rows)


@component
class ODSToDocument:
    """Converts ODS spreadsheet files to Haystack Documents.

    Each sheet in the ODS file becomes a separate Document.
    Cell values are tab-separated, rows are newline-separated.

    Metadata includes: file_path, sheet_name, file_type.
    """

    @component.output_types(documents=list[Document])
    def run(self, sources: list[Union[str, Path]]) -> dict[str, list[Document]]:
        """Convert ODS files to Documents.

        Args:
            sources: List of file paths to ODS files.

        Returns:
            Dictionary with 'documents' key containing list of Documents.
        """
        documents: list[Document] = []

        for source in sources:
            source_path = Path(source)
            if not source_path.exists():
                logger.warning("ODS file not found: %s", source_path)
                continue

            try:
                doc = odf_load(str(source_path))
            except Exception:
                logger.exception("Failed to load ODS file: %s", source_path)
                continue

            sheets = doc.spreadsheet.getElementsByType(Table)

            for sheet in sheets:
                sheet_name = sheet.getAttribute("name") or "Sheet"
                content = _extract_sheet_content(sheet)

                if not content.strip():
                    logger.debug("Skipping empty sheet '%s' in %s", sheet_name, source_path)
                    continue

                documents.append(
                    Document(
                        content=content,
                        meta={
                            "file_path": str(source_path),
                            "sheet_name": sheet_name,
                            "file_type": "ods",
                        },
                    )
                )

        return {"documents": documents}
