"""This module is a rewrite of remarks/remarks.py to avoid dealing with obsidian and have unwanted suffixes"""

import logging
import os
import pathlib
import sys
import tempfile
import zipfile

import fitz  # PyMuPDF
from fitz import Page, Rect, Annot, Quad

from rmscene.scene_items import PenColor
from rmc.exporters.pdf import rm_to_pdf
from rmc.exporters.svg import build_anchor_pos, get_bounding_box, set_device, set_dimensions_for_pdf, rmc_config

from remarks.Document import Document
from remarks.conversion.parsing import (
    parse_rm_file,
    read_rm_file_version,
)
from remarks.metadata import ReMarkableAnnotationsFileHeaderVersion
from remarks.utils import (
    is_document,
    get_document_filetype,
    get_visible_name,
    get_ui_path,
)
from remarks.warnings import scrybble_warning_only_v6_supported
from remarks.output.PdfFile import add_error_annotation
from remarks.conversion.parsing import RemarksRectangle

from .output.org import OrgSerializer

HARDCODED_COLORMAP = {
    (245, 206, 39, 255): PenColor.HIGHLIGHT_YELLOW,
    (39, 155, 245, 255): PenColor.HIGHLIGHT_BLUE,
    (228, 39, 245, 255): PenColor.HIGHLIGHT_PINK,
    (245, 142, 39, 255): PenColor.HIGHLIGHT_ORANGE,
    (19, 151, 7, 255): PenColor.HIGHLIGHT_GREEN,
    (199, 199, 198, 255): PenColor.HIGHLIGHT_GRAY,
    (33, 30, 28, 64): PenColor.SHADER_GRAY,
    (254, 178, 0, 115): PenColor.SHADER_ORANGE,
    (192, 127, 210, 128): PenColor.SHADER_MAGENTA,
    (48, 74, 224, 77): PenColor.SHADER_BLUE,
    (194, 49, 50, 102): PenColor.SHADER_RED,
    (145, 218, 113, 128): PenColor.SHADER_GREEN,
    (250, 231, 25, 115): PenColor.SHADER_YELLOW,
    (116, 210, 232, 102): PenColor.SHADER_CYAN,
}


HARDCODED_COLORMAP = {
    (19, 151, 7, 255): PenColor.HIGHLIGHT_YELLOW,
    (19, 151, 7, 255): PenColor.HIGHLIGHT_BLUE,
    (19, 151, 7, 255): PenColor.HIGHLIGHT_PINK,
    (19, 151, 7, 255): PenColor.HIGHLIGHT_ORANGE,
    (19, 151, 7, 255): PenColor.HIGHLIGHT_GREEN,
    (19, 151, 7, 255): PenColor.HIGHLIGHT_GRAY,
    (19, 151, 7, 255): PenColor.SHADER_GRAY,
    (19, 151, 7, 255): PenColor.SHADER_ORANGE,
    (19, 151, 7, 255): PenColor.SHADER_MAGENTA,
    (19, 151, 7, 255): PenColor.SHADER_BLUE,
    (19, 151, 7, 255): PenColor.SHADER_GREEN,
    (19, 151, 7, 255): PenColor.SHADER_YELLOW,
    (19, 151, 7, 255): PenColor.SHADER_CYAN,
}


def get_highlight_color(pen_color: int) -> tuple[float, float, float]:
    """Convert PenColor enum value to RGB tuple for PDF annotations.

    Args:
        pen_color: PenColor enum value from rmscene

    Returns:
        RGB tuple with values normalized to 0-1 range for PyMuPDF
    """
    # Create reverse mapping from PenColor to RGBA
    color_to_rgba = {v: k for k, v in HARDCODED_COLORMAP.items()}

    # Try to convert to PenColor enum, fall back to raw integer lookup
    try:
        pen_color_enum = PenColor(pen_color)
        rgba = color_to_rgba.get(pen_color_enum, (19, 151, 7, 255))
    except ValueError:
        # If the color value is not a valid PenColor enum, use fallback
        rgba = (19, 151, 7, 255)

    # Convert to RGB (ignore alpha) and normalize to 0-1 range
    r, g, b, _ = rgba
    return (r / 255, g / 255, b / 255)


def apply_smart_highlight(page: Page, highlight: RemarksRectangle, x_translation: float) -> None:
    # Get the color for this highlight based on its PenColor value
    highlight_color = get_highlight_color(highlight.color)
    for rectangle in highlight.rectangles:
        x, y, w, h = rectangle.x, rectangle.y, rectangle.w, rectangle.h
        # Highlight rectangles are already in PDF coordinate space via xx/yy transformation
        # x_translation positions them correctly relative to reMarkable's (0,0) at center-top of PDF
        rect = Rect((x + x_translation, y), (x + x_translation + w, y + h))
        try:
            annot: Annot = page.add_highlight_annot(quads=rect)
            # Use the dynamic color based on the highlight's actual color from the reMarkable file
            annot.set_colors(stroke=highlight_color)
            annot.set_opacity(0.3)
            annot.update()
        except ValueError:
            logging.warning(f"Bad quads entry {rect}")


class Remarks:
    def __init__(self):
        self._logger = logging.getLogger(self.__class__.__name__)

    def run(self, input_dir: pathlib.Path, output_dir: pathlib.Path, override: bool = False):
        if input_dir.name.endswith(".rmn") or input_dir.name.endswith(".rmdoc"):
            temp_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(input_dir, "r") as zip_ref:
                zip_ref.extractall(temp_dir)
            input_dir = pathlib.Path(temp_dir)

        docs = list(input_dir.glob("*.metadata"))
        num_docs = len(docs)
        if num_docs == 0:
            self._logger.warning(
                f'No .metadata files found in "{input_dir}". Are you sure you\'re running remarks on a valid xochitl-like directory? See: https://github.com/lucasrla/remarks#1-copy-remarkables-raw-document-files-to-your-computer'
            )
            sys.exit(1)

        self._logger.info(
            f'Found {num_docs} documents in "{input_dir}", will process them now',
        )

        for metadata_path in docs:
            if not is_document(metadata_path):
                continue

            doc_type = get_document_filetype(metadata_path)
            # Both "Quick Sheets" and "Notebooks" have doc_type="notebook"
            supported_types = ["pdf", "epub", "notebook"]

            doc_name = get_visible_name(metadata_path)

            if not doc_name:
                continue

            if doc_type in supported_types:
                self._logger.info(f'File: "{doc_name} [type={doc_type}]" ({metadata_path.stem})')

                in_device_dir = get_ui_path(metadata_path)
                relative_doc_path = pathlib.Path(f"{in_device_dir}/{doc_name}")

                self._process_document(metadata_path, relative_doc_path, output_dir, override)
            else:
                self._logger.info(
                    f'File skipped: "{doc_name}" ({metadata_path.stem}) due to unsupported filetype: {doc_type}. remarks only supports: {", ".join(supported_types)}'
                )

        self._logger.info(
            f'Done processing "{input_dir}"',
        )

    def _process_document(
        self,
        metadata_path: pathlib.Path,
        relative_doc_path: pathlib.Path,
        output_dir: pathlib.Path,
        override: bool = False,
        device: str | None = None,
    ):

        document = Document(metadata_path)
        rmc_pdf_src = document.open_source_pdf()

        org_serializer = OrgSerializer(document)

        for (
            page_uuid,
            page_idx,
            rm_annotation_file,
        ) in document.pages():
            self._logger.info(f"processing page {page_idx + 1}, {page_uuid}")
            page = rmc_pdf_src[page_idx]

            rm_file_version = read_rm_file_version(rm_annotation_file)

            if rm_file_version == ReMarkableAnnotationsFileHeaderVersion.V6:
                # Get PDF page dimensions BEFORE parsing to ensure correct SCALE is used
                page_rotation = page.rotation
                page.set_rotation(0)
                w_bg, h_bg = page.cropbox.width, page.cropbox.height
                if int(page_rotation) in [90, 270]:
                    w_bg, h_bg = h_bg, w_bg
                page.set_rotation(page_rotation)  # Restore rotation

                # Set SVG dimensions: use PDF dimensions if there's backing content,
                # otherwise use device setting for notebooks
                has_backing_pdf = page.get_contents()
                if has_backing_pdf:
                    self._logger.info(f"Setting page dimensions based on pdf: {round(w_bg,2)} x {round(h_bg,2)}")
                    set_dimensions_for_pdf(w_bg, h_bg)
                elif device:
                    self._logger.info(f"Setting page dimensions based on device: {device}")
                    set_device(device)
                else:
                    self._logger.warning(
                        f"Unknown device and no backing pdf: setting page size to RMPP (if this is incorrect, specify device with --device)"
                    )
                    set_device("RMPP")

                (ann_data, has_ann_hl), version = parse_rm_file(rm_annotation_file)
                temp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", mode="w", delete=False)

                # This offset is used for smart highlights
                highlights_x_translation = 0
                try:

                    # convert the pdf
                    rm_to_pdf(rm_annotation_file, temp_pdf.name)

                    svg_pdf = fitz.open(temp_pdf.name)

                    # if the background page is not empty, need to merge svg on top of background page
                    if has_backing_pdf:
                        # w_bg, h_bg already calculated above
                        # find the (top, right) coordinates of the svg
                        anchor_pos = build_anchor_pos(ann_data["scene_tree"].root_text)
                        # Convert PDF dimensions to screen coordinates for bounding box default
                        # PDF uses points (72 DPI), screen uses device DPI; SCALE = 72/DPI
                        # reMarkable uses center-top origin: x from -w/2 to w/2, y from 0 to h
                        w_bg_screen = w_bg / rmc_config.scale
                        h_bg_screen = h_bg / rmc_config.scale
                        pdf_default_bounds = (-w_bg_screen / 2, w_bg_screen / 2, 0, h_bg_screen)
                        x_min, x_max, y_min, y_max = get_bounding_box(
                            ann_data["scene_tree"].root, anchor_pos, default=pdf_default_bounds
                        )
                        x_shift, y_shift, w_svg, h_svg = (
                            rmc_config.xx(x_min),
                            rmc_config.yy(y_min),
                            rmc_config.xx(x_max - x_min + 1),
                            rmc_config.yy(y_max - y_min + 1),
                        )

                        # compute the width/height of a blank page that can contain both svg and background pdf
                        width, height = max(w_svg, w_bg), max(h_svg, h_bg)
                        # compute position of svg and background in the new_page
                        # reMarkable (0,0) is at center-top of PDF page
                        # SVG coordinates need to be positioned relative to this center-top origin
                        x_svg, y_svg = 0, 0
                        x_bg, y_bg = 0, 0

                        if w_svg > w_bg:
                            x_bg = width / 2 - w_bg / 2 - (w_svg / 2 + x_shift)
                            # Highlights need to account for reMarkable's center-top origin: PDF center = w_bg/2
                            highlights_x_translation = x_bg + w_bg / 2
                        elif w_svg < w_bg:
                            x_svg = width / 2 - w_svg / 2 + (w_svg / 2 + x_shift)
                            # When SVG is smaller, PDF spans full width, so center is at w_bg/2
                            highlights_x_translation = w_bg / 2
                        if h_svg > h_bg:
                            y_bg = -y_shift
                        elif h_svg < h_bg:
                            y_svg = y_shift

                        # create the merged page in independent document as show_pdf_page can't be done on the same document
                        doc = fitz.open()
                        page = doc.new_page(-1, width=width, height=height)
                        page.show_pdf_page(
                            fitz.Rect(x_bg, y_bg, x_bg + w_bg, y_bg + h_bg),
                            rmc_pdf_src,
                            page_idx,
                            rotate=-page_rotation,
                        )
                        page.show_pdf_page(fitz.Rect(x_svg, y_svg, x_svg + w_svg, y_svg + h_svg), svg_pdf, 0)

                        rmc_pdf_src.insert_pdf(doc, start_at=page_idx)
                    else:
                        rmc_pdf_src.insert_pdf(svg_pdf, start_at=page_idx)
                    rmc_pdf_src.delete_page(page_idx + 1)
                except AttributeError:
                    add_error_annotation(page)
                finally:
                    temp_pdf.close()
                    os.remove(temp_pdf.name)

                if ann_data:
                    if "text" in ann_data:
                        org_serializer.add_text(page_idx, ann_data["text"])
                    if "glyph_ranges" in ann_data:
                        org_serializer.add_highlights(page_idx, ann_data["glyph_ranges"])
                    if ann_data["highlights"]:
                        for highlight in ann_data["highlights"]:
                            apply_smart_highlight(rmc_pdf_src[page_idx], highlight, highlights_x_translation)
            else:
                scrybble_warning_only_v6_supported.render_as_annotation(page)

        output_path = output_dir / relative_doc_path
        if output_path.exists():
            if override:
                output_path.unlink()
            else:
                raise Exception(f"{output_path} already exists and override is not activated")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix == ".pdf":
            rmc_pdf_src.save(output_path)
        else:
            rmc_pdf_src.save(f"{output_path.resolve()}.pdf")

        org_serializer.save(output_path.with_suffix(".org"))
