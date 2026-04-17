from typing import Optional
from models.llm_message import LLMSystemMessage, LLMUserMessage
from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import PresentationOutlineModel
from services.llm_client import LLMClient
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_provider import get_model
from utils.get_dynamic_models import get_presentation_structure_model_with_n_slides
from models.presentation_structure_model import PresentationStructureModel


def get_messages(
    presentation_layout: PresentationLayoutModel,
    n_slides: int,
    data: str,
    instructions: Optional[str] = None,
):
    return [
        LLMSystemMessage(
            content=f"""
                You're a professional presentation designer with creative freedom to design engaging presentations.

                {presentation_layout.to_string()}

                # DESIGN PHILOSOPHY
                - Create visually compelling and varied presentations
                - Match layout to content purpose and audience needs
                - Prioritize engagement over rigid formatting rules

                # Layout Selection Guidelines
                1. **Content-driven choices**: Let the slide's purpose guide layout selection
                - Opening/closing → Title layouts
                - Processes/workflows → Visual process layouts  
                - Comparisons/contrasts → Side-by-side layouts
                - Data/metrics → Chart/graph layouts
                - Concepts/ideas → Image + text layouts
                - Key insights → Emphasis layouts

                2. **Visual variety**: Aim for diverse, engaging presentation flow
                - Mix text-heavy and visual-heavy slides naturally
                - Use your judgment on when repetition serves the content
                - Balance information density across slides

                3. **Audience experience**: Consider how slides work together
                - Create natural transitions between topics
                - Use layouts that enhance comprehension
                - Design for maximum impact and retention

                **Trust your design instincts. Focus on creating the most effective presentation for the content and audience.**

                {"# User Instruction:" if instructions else ""}
                {instructions or ""}

                User intruction should be taken into account while creating the presentation structure, except for number of slides.

                Select layout index for each of the {n_slides} slides based on what will best serve the presentation's goals.
            """,
        ),
        LLMUserMessage(
            content=f"""
                {data}
            """,
        ),
    ]


def get_messages_for_slides_markdown(
    presentation_layout: PresentationLayoutModel,
    n_slides: int,
    data: str,
    instructions: Optional[str] = None,
):
    return [
        LLMSystemMessage(
            content=f"""
                You're a professional presentation designer with creative freedom to design engaging presentations.

                {"# User Instruction:" if instructions else ""}
                {instructions or ""}

                {presentation_layout.to_string()}

                Select layout that best matches the content of the slides.

                User intruction should be taken into account while creating the presentation structure, except for number of slides.

                Select layout index for each of the {n_slides} slides based on what will best serve the presentation's goals.
            """,
        ),
        LLMUserMessage(
            content=f"""
                {data}
            """,
        ),
    ]


async def generate_presentation_structure(
    presentation_outline: PresentationOutlineModel,
    presentation_layout: PresentationLayoutModel,
    instructions: Optional[str] = None,
    using_slides_markdown: bool = False,
) -> PresentationStructureModel:

    import asyncio, random
    from constants.llm import FALLBACK_GOOGLE_MODEL

    model = get_model()
    response_model = get_presentation_structure_model_with_n_slides(
        len(presentation_outline.slides)
    )

    max_retries = 10
    fallback_after = 5
    current_model = model
    messages = (
        get_messages_for_slides_markdown(
            presentation_layout,
            len(presentation_outline.slides),
            presentation_outline.to_string(),
            instructions,
        )
        if using_slides_markdown
        else get_messages(
            presentation_layout,
            len(presentation_outline.slides),
            presentation_outline.to_string(),
            instructions,
        )
    )

    for attempt in range(max_retries):
        try:
            client = LLMClient()
            response = await client.generate_structured(
                model=current_model,
                messages=messages,
                response_format=response_model.model_json_schema(),
                strict=True,
            )
            result = PresentationStructureModel(**response)
            n_slides = len(presentation_outline.slides)
            if len(result.slides) > n_slides:
                result.slides = result.slides[:n_slides]
            return result
        except Exception as e:
            error_msg = str(e).lower()
            if "404" in error_msg and current_model == FALLBACK_GOOGLE_MODEL:
                print(f"Fallback model {FALLBACK_GOOGLE_MODEL} not available, reverting to {model}")
                current_model = model
            is_retryable = (
                "503" in error_msg or "429" in error_msg
                or "high demand" in error_msg or "service unavailable" in error_msg
                or "404" in error_msg or "403" in error_msg
                or "permission_denied" in error_msg
            )
            if is_retryable and attempt < max_retries - 1:
                if attempt + 1 >= fallback_after and current_model != FALLBACK_GOOGLE_MODEL and "404" not in error_msg:
                    print(f"Structure: switching to fallback model {FALLBACK_GOOGLE_MODEL} after {attempt + 1} failures")
                    current_model = FALLBACK_GOOGLE_MODEL
                wait_time = min(2 ** attempt + random.uniform(0, 1), 30)
                print(f"Structure generation failed (attempt {attempt + 1}/{max_retries}): retrying in {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
                continue
            raise handle_llm_client_exceptions(e)
