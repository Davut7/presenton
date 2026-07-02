import asyncio
import json
import os
import aiohttp
from typing import Literal
import uuid
from fastapi import HTTPException
from pathvalidate import sanitize_filename

from models.pptx_models import PptxPresentationModel
from models.presentation_and_path import PresentationAndPath
from services.pptx_presentation_creator import PptxPresentationCreator
from services.temp_file_service import TEMP_FILE_SERVICE
from utils.asset_directory_utils import get_exports_directory
import uuid


# Export goes through a single headless Chrome (Puppeteer) behind the Next.js
# service. Dozens of parallel exports make Chrome thrash — each render takes
# minutes and the whole batch stalls. Cap concurrent exports so Chrome stays
# healthy; queued exports wait a few seconds instead of all degrading at once.
_EXPORT_CONCURRENCY = int(os.getenv("EXPORT_CONCURRENCY", "3") or 3)
# Hard ceiling on a single export round-trip to the Next.js/Puppeteer service.
# Without it a wedged Chrome render leaves the aiohttp GET hanging forever,
# which freezes the task's `updated_at` and trips the external watchdog.
_EXPORT_HTTP_TIMEOUT = int(os.getenv("EXPORT_HTTP_TIMEOUT", "300") or 300)
_export_semaphore: asyncio.Semaphore | None = None


def _get_export_semaphore() -> asyncio.Semaphore:
    global _export_semaphore
    if _export_semaphore is None:
        _export_semaphore = asyncio.Semaphore(max(1, _EXPORT_CONCURRENCY))
    return _export_semaphore


async def export_presentation(
    presentation_id: uuid.UUID, title: str, export_as: Literal["pptx", "pdf"]
) -> PresentationAndPath:
    _timeout = aiohttp.ClientTimeout(total=_EXPORT_HTTP_TIMEOUT)
    async with _get_export_semaphore():
        if export_as == "pptx":

            # Get the converted PPTX model from the Next.js service
            async with aiohttp.ClientSession(timeout=_timeout) as session:
                async with session.get(
                    f"http://localhost/api/presentation_to_pptx_model?id={presentation_id}"
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"Failed to get PPTX model: {error_text}")
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to convert presentation to PPTX model",
                        )
                    pptx_model_data = await response.json()

            # Create PPTX file using the converted model
            pptx_model = PptxPresentationModel(**pptx_model_data)
            temp_dir = TEMP_FILE_SERVICE.create_temp_dir()
            pptx_creator = PptxPresentationCreator(pptx_model, temp_dir)
            await pptx_creator.create_ppt()

            export_directory = get_exports_directory()
            pptx_path = os.path.join(
                export_directory,
                f"{sanitize_filename(title or str(uuid.uuid4()))}.pptx",
            )
            pptx_creator.save(pptx_path)

            return PresentationAndPath(
                presentation_id=presentation_id,
                path=pptx_path,
            )
        else:
            async with aiohttp.ClientSession(timeout=_timeout) as session:
                async with session.post(
                    "http://localhost/api/export-as-pdf",
                    json={
                        "id": str(presentation_id),
                        "title": sanitize_filename(title or str(uuid.uuid4())),
                    },
                ) as response:
                    response_json = await response.json()

            return PresentationAndPath(
                presentation_id=presentation_id,
                path=response_json["path"],
            )
