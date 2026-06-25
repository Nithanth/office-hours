"""Figure/image extraction from PDF papers with optional vision-LLM descriptions.

Extracts raster images and page-level renders from PDFs using PyMuPDF,
caches them on disk, and optionally generates text descriptions via a
vision-capable LLM for downstream indexing and RAG retrieval.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fitz
from loguru import logger

from config import config

if TYPE_CHECKING:
    from services.core.llm_service import LLMService

logger = logger.bind(component="FigureExtractor")

# ---------------------------------------------------------------------------
# Vision LLM prompt for describing figures
# ---------------------------------------------------------------------------
_VISION_PROMPT = (
    "You are analyzing a figure from an academic research paper. "
    "Describe what this figure shows, including: "
    "1) The type of visualization (plot, diagram, table, photo, etc.), "
    "2) What data or concepts it represents, "
    "3) Key findings or trends visible, "
    "4) Any axis labels, legends, or annotations. "
    "Be concise but thorough (2-4 sentences)."
)

# Minimum pixel dimensions to keep an extracted image (skip tiny icons/borders).
_MIN_WIDTH = 100
_MIN_HEIGHT = 100


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class FigureInfo:
    """Metadata and path for a single extracted figure."""

    paper_id: str
    index: int  # 0-based figure number
    page_number: int  # PDF page where it was found
    image_path: str  # absolute path to saved PNG
    width: int
    height: int
    description: str = ""  # vision LLM description (empty until describe_figures called)
    caption: str = ""  # extracted caption text if found near the figure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _figures_dir(paper_id: str) -> Path:
    """Return the cache directory for a given paper's figures."""
    return config.CACHE_DIR / "figures" / paper_id


def _metadata_path(paper_id: str) -> Path:
    """Return the path to the metadata JSON file for a paper's figures."""
    return _figures_dir(paper_id) / "metadata.json"


def _is_mostly_single_color(pixmap: fitz.Pixmap, threshold: float = 0.97) -> bool:
    """Return True if the pixmap is dominated by a single color (likely background).

    Samples a subset of pixels and checks whether a single value dominates.
    This avoids keeping blank/white/black decorative rectangles.
    """
    try:
        samples = pixmap.samples
        if not samples:
            return True

        total_pixels = pixmap.width * pixmap.height
        if total_pixels == 0:
            return True

        # Count bytes that match the first pixel's value.
        # For speed, compare raw sample bytes in chunks of n (channels).
        n = pixmap.n  # number of components per pixel (e.g. 3 for RGB, 4 for RGBA)
        first_pixel = samples[:n]

        # Sample up to 10 000 evenly spaced pixels to avoid processing huge images.
        step = max(1, total_pixels // 10_000)
        matches = 0
        checked = 0
        for i in range(0, total_pixels, step):
            offset = i * n
            if samples[offset : offset + n] == first_pixel:
                matches += 1
            checked += 1

        if checked == 0:
            return True
        return (matches / checked) >= threshold
    except Exception:
        # If anything goes wrong, assume it's NOT single-color (keep the image).
        return False


def _serialize_figures(figures: list[FigureInfo]) -> str:
    """Serialize a list of FigureInfo to JSON string."""
    return json.dumps([asdict(f) for f in figures], indent=2)


def _deserialize_figures(data: str) -> list[FigureInfo]:
    """Deserialize a JSON string into a list of FigureInfo objects."""
    items = json.loads(data)
    return [FigureInfo(**item) for item in items]


# ---------------------------------------------------------------------------
# Main extractor class
# ---------------------------------------------------------------------------


class FigureExtractor:
    """Extracts figures from PDF papers and optionally describes them with a vision LLM."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_figures(self, pdf_path: str | Path, paper_id: str) -> list[FigureInfo]:
        """Extract images/figures from a PDF file.

        Uses PyMuPDF to iterate over each page, extract embedded raster
        images, filter out tiny or decorative elements, and save the
        remaining images as PNGs on disk.

        Args:
            pdf_path: Path to the PDF file on disk.
            paper_id: Unique identifier for the paper (used for caching).

        Returns:
            List of FigureInfo dataclass instances describing each extracted figure.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            logger.warning("PDF file not found", path=str(pdf_path), paper_id=paper_id)
            return []

        output_dir = _figures_dir(paper_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        figures: list[FigureInfo] = []
        fig_index = 0

        try:
            doc = fitz.open(str(pdf_path))
        except Exception as exc:
            logger.warning(
                "Failed to open PDF for figure extraction",
                path=str(pdf_path),
                paper_id=paper_id,
                error=str(exc),
            )
            return []

        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)

                for img_info in image_list:
                    xref = img_info[0]

                    try:
                        pixmap = fitz.Pixmap(doc, xref)
                    except Exception as exc:
                        logger.debug(
                            "Could not extract pixmap",
                            xref=xref,
                            page=page_num,
                            error=str(exc),
                        )
                        continue

                    # Convert CMYK or other colorspaces to RGB for PNG export.
                    if pixmap.n - pixmap.alpha > 3:
                        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)

                    # --- Filtering ---
                    # Skip images that are too small (icons, bullets, line art).
                    if pixmap.width < _MIN_WIDTH or pixmap.height < _MIN_HEIGHT:
                        continue

                    # Skip images that are mostly a single color (backgrounds/borders).
                    if _is_mostly_single_color(pixmap):
                        logger.debug(
                            "Skipping single-color image",
                            xref=xref,
                            page=page_num,
                            size=f"{pixmap.width}x{pixmap.height}",
                        )
                        continue

                    # --- Save as PNG ---
                    png_path = output_dir / f"{fig_index}.png"
                    try:
                        png_bytes = pixmap.tobytes("png")
                        png_path.write_bytes(png_bytes)
                    except Exception as exc:
                        logger.warning(
                            "Failed to save figure PNG",
                            fig_index=fig_index,
                            page=page_num,
                            error=str(exc),
                        )
                        continue

                    # --- Attempt to extract nearby caption text ---
                    caption = self._extract_caption_near_image(page, img_info)

                    figure = FigureInfo(
                        paper_id=paper_id,
                        index=fig_index,
                        page_number=page_num + 1,  # 1-based for human readability
                        image_path=str(png_path.resolve()),
                        width=pixmap.width,
                        height=pixmap.height,
                        caption=caption,
                    )
                    figures.append(figure)
                    fig_index += 1

        finally:
            doc.close()

        # Persist metadata cache.
        self._save_metadata(paper_id, figures)

        logger.info(
            "Figures extracted",
            paper_id=paper_id,
            count=len(figures),
        )
        return figures

    async def describe_figures(
        self,
        figures: list[FigureInfo],
        paper_id: str,
        *,
        llm_service: LLMService | None = None,
        model_key: str | None = None,
    ) -> list[FigureInfo]:
        """Generate text descriptions for extracted figures using a vision LLM.

        Sends each figure image (base64-encoded) to a vision-capable LLM
        along with a structured prompt, then caches the descriptions on disk.

        Args:
            figures: List of FigureInfo objects (from extract_figures).
            paper_id: Paper identifier for cache key.
            llm_service: Optional LLMService instance. If None, descriptions
                are skipped and figures are returned as-is.
            model_key: Optional model key to select a specific vision-capable
                model. Defaults to the provider's default model.

        Returns:
            The same list of FigureInfo objects, updated with descriptions
            where generation succeeded.
        """
        if not figures:
            return figures

        if llm_service is None:
            logger.debug("No LLM service provided; skipping figure descriptions", paper_id=paper_id)
            return figures

        # Determine which provider/model to use for vision.
        # Default to OpenAI (GPT-4o has strong vision), fall back to anthropic or gemini.
        provider_name, model_name = self._resolve_vision_model(llm_service, model_key)
        if provider_name is None:
            logger.warning(
                "No vision-capable provider available; skipping descriptions",
                paper_id=paper_id,
            )
            return figures

        for figure in figures:
            # Skip if already described (e.g. loaded from cache).
            if figure.description:
                continue

            try:
                image_b64 = self._encode_image_base64(figure.image_path)
                if not image_b64:
                    continue

                # Build the prompt with context about any existing caption.
                prompt = _VISION_PROMPT
                if figure.caption:
                    prompt += f"\n\nThe figure has the following caption: \"{figure.caption}\""

                # TODO: The current LLMProvider.generate_text interface does not
                # natively support multimodal (image) inputs. When the provider
                # base class is extended with an `images` parameter, replace the
                # provider-specific logic below with a unified call like:
                #   await llm_service.generate_completion(
                #       provider_name=provider_name,
                #       model_name=model_name,
                #       prompt=prompt,
                #       max_tokens=300,
                #       temperature=0.3,
                #       images=[image_b64],
                #   )

                description = await self._call_vision_llm(
                    llm_service=llm_service,
                    provider_name=provider_name,
                    model_name=model_name,
                    prompt=prompt,
                    image_b64=image_b64,
                )
                figure.description = description

            except Exception as exc:
                logger.warning(
                    "Failed to generate description for figure",
                    paper_id=paper_id,
                    fig_index=figure.index,
                    error=str(exc),
                )
                # Non-fatal: figure still exists, just without a description.
                continue

        # Update the cached metadata with descriptions.
        self._save_metadata(paper_id, figures)

        described_count = sum(1 for f in figures if f.description)
        logger.info(
            "Figure descriptions generated",
            paper_id=paper_id,
            described=described_count,
            total=len(figures),
        )
        return figures

    def get_cached_figures(self, paper_id: str) -> list[FigureInfo] | None:
        """Return previously extracted and described figures from cache.

        Args:
            paper_id: Paper identifier to look up.

        Returns:
            List of FigureInfo if cached metadata exists, otherwise None.
        """
        meta_path = _metadata_path(paper_id)
        if not meta_path.exists():
            return None

        try:
            data = meta_path.read_text(encoding="utf-8")
            figures = _deserialize_figures(data)
            # Verify that at least the image files still exist on disk.
            valid = [f for f in figures if Path(f.image_path).exists()]
            if len(valid) < len(figures):
                logger.debug(
                    "Some cached figure images missing; returning valid subset",
                    paper_id=paper_id,
                    cached=len(figures),
                    valid=len(valid),
                )
            return valid if valid else None
        except Exception as exc:
            logger.warning(
                "Failed to load cached figure metadata",
                paper_id=paper_id,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_caption_near_image(self, page: Any, img_info: tuple) -> str:
        """Attempt to extract caption text near an image on the page.

        Looks for text blocks containing 'Figure' or 'Fig.' patterns
        that are spatially close to the image bounding box.
        """
        import re

        try:
            # Get the image's bounding box on the page.
            img_rects = page.get_image_rects(img_info)
            if not img_rects:
                return ""

            img_rect = img_rects[0]
            # Search for text below the image (captions are typically underneath).
            # Expand search area below the image by a generous margin.
            search_rect = fitz.Rect(
                img_rect.x0 - 20,
                img_rect.y1,
                img_rect.x1 + 20,
                img_rect.y1 + 80,
            )

            blocks = page.get_text("blocks", clip=search_rect)
            caption_pattern = re.compile(
                r"(?:Figure|Fig\.)\s*\d+", re.IGNORECASE
            )

            for block in blocks:
                text = block[4].strip() if len(block) > 4 else ""
                if caption_pattern.search(text):
                    # Clean up whitespace and return the caption.
                    return " ".join(text.split())

        except Exception:
            # Caption extraction is best-effort; don't fail the whole pipeline.
            pass

        return ""

    def _save_metadata(self, paper_id: str, figures: list[FigureInfo]) -> None:
        """Persist figure metadata as JSON alongside the PNGs."""
        meta_path = _metadata_path(paper_id)
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(_serialize_figures(figures), encoding="utf-8")
        except Exception as exc:
            logger.warning(
                "Failed to save figure metadata",
                paper_id=paper_id,
                error=str(exc),
            )

    @staticmethod
    def _encode_image_base64(image_path: str) -> str:
        """Read an image file and return its base64-encoded string."""
        path = Path(image_path)
        if not path.exists():
            logger.warning("Image file not found for encoding", path=image_path)
            return ""
        return base64.b64encode(path.read_bytes()).decode("utf-8")

    @staticmethod
    def _resolve_vision_model(
        llm_service: LLMService, model_key: str | None
    ) -> tuple[str | None, str | None]:
        """Determine the best available vision-capable provider and model.

        Priority: OpenAI (GPT-4o) > Anthropic (Claude) > Gemini.
        """
        # If a specific model_key is given, look it up in available models.
        if model_key:
            model_info = config.AVAILABLE_MODELS.get(model_key)
            if model_info:
                provider = model_info.get("provider")
                model_id = model_info.get("id")
                if provider and llm_service.is_provider_initialized(provider):
                    return str(provider), str(model_id)

        # Auto-detect: prefer providers with known vision capability.
        vision_priority = ["openai", "anthropic", "gemini"]
        for provider_name in vision_priority:
            if llm_service.is_provider_initialized(provider_name):
                # Use the provider's default model (assumed vision-capable for these).
                provider_obj = llm_service.providers.get(provider_name)
                if provider_obj:
                    return provider_name, provider_obj.default_model

        return None, None

    @staticmethod
    async def _call_vision_llm(
        llm_service: LLMService,
        provider_name: str,
        model_name: str,
        prompt: str,
        image_b64: str,
    ) -> str:
        """Send an image to a vision-capable LLM and return the description.

        Constructs provider-specific multimodal message payloads since the
        current LLMProvider base class does not have a unified vision API.
        """
        provider = llm_service.providers.get(provider_name)
        if provider is None:
            return ""

        # Ensure client is ready.
        await provider.initialize_client()

        if provider_name == "openai":
            return await FigureExtractor._call_openai_vision(
                provider, model_name, prompt, image_b64
            )
        elif provider_name == "anthropic":
            return await FigureExtractor._call_anthropic_vision(
                provider, model_name, prompt, image_b64
            )
        elif provider_name == "gemini":
            return await FigureExtractor._call_gemini_vision(
                provider, model_name, prompt, image_b64
            )
        else:
            # Fallback: attempt text-only generation with image context in prompt.
            logger.debug("Vision not supported for provider; using text-only fallback", provider=provider_name)
            return await provider.generate_text(
                prompt=f"{prompt}\n\n[Image content not available for text-only model]",
                model=model_name,
                max_tokens=300,
                temperature=0.3,
            )

    @staticmethod
    async def _call_openai_vision(
        provider: Any, model_name: str, prompt: str, image_b64: str
    ) -> str:
        """Call OpenAI's vision API with a base64-encoded image."""
        from openai import AsyncOpenAI

        client: AsyncOpenAI = provider._async_client
        if client is None:
            return ""

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                            "detail": "low",
                        },
                    },
                ],
            }
        ]

        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=300,
            temperature=0.3,
        )

        if response.choices and response.choices[0].message.content:
            return response.choices[0].message.content.strip()
        return ""

    @staticmethod
    async def _call_anthropic_vision(
        provider: Any, model_name: str, prompt: str, image_b64: str
    ) -> str:
        """Call Anthropic's vision API with a base64-encoded image."""
        from anthropic import AsyncAnthropic

        client: AsyncAnthropic = provider._async_client
        if client is None:
            return ""

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        response = await client.messages.create(
            model=model_name,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=300,
            temperature=0.3,
        )

        if response.content and len(response.content) > 0:
            return response.content[0].text.strip()
        return ""

    @staticmethod
    async def _call_gemini_vision(
        provider: Any, model_name: str, prompt: str, image_b64: str
    ) -> str:
        """Call Gemini's vision API with a base64-encoded image."""
        gm_client = provider._async_client
        if gm_client is None:
            return ""

        from google.genai import types

        # Construct multimodal content with inline image data.
        image_part = types.Part.from_bytes(
            data=base64.b64decode(image_b64),
            mime_type="image/png",
        )

        response = await gm_client.models.generate_content(
            model=model_name,
            contents=[prompt, image_part],
        )

        if getattr(response, "text", ""):
            return str(response.text).strip()
        return ""
