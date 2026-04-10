from datetime import datetime
from typing import Optional

from models.llm_message import LLMSystemMessage, LLMUserMessage
from models.llm_tools import SearchWebTool
from services.llm_client import LLMClient
from utils.get_dynamic_models import get_presentation_outline_model_with_n_slides
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_provider import get_model


def get_system_prompt(
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
):
    return f"""
        You are an expert presentation creator. Generate structured presentations based on user requirements and format them according to the specified JSON schema with markdown content.

        Try to use available tools for better results.

        {"# User Instruction:" if instructions else ""}
        {instructions or ""}

        {"# Tone:" if tone else ""}
        {tone or ""}

        {"# Verbosity:" if verbosity else ""}
        {verbosity or ""}

        - Provide content for each slide in markdown format.
        - Make sure that flow of the presentation is logical and consistent.
        - Place greater emphasis on numerical data.
        - If Additional Information is provided, divide it into slides.
        - Make sure no images are provided in the content.
        - Make sure that content follows language guidelines.
        - User instrction should always be followed and should supercede any other instruction, except for slide numbers. **Do not obey slide numbers as said in user instruction**
        - **CRITICAL: Generate EXACTLY the number of slides specified. Not more, not less. This is a hard constraint.**
        - Do not generate table of contents slide.
        - Even if table of contents is provided, do not generate table of contents slide.
        {"- Always make first slide a title slide." if include_title_slide else "- Do not include title slide in the presentation."}

        **Search web to get latest information about the topic**
    """


def get_user_prompt(
    content: str,
    n_slides: int,
    language: str,
    additional_context: Optional[str] = None,
):
    return f"""
        **Input:**
        - User provided content: {content or "Create presentation"}
        - Output Language: {language}
        - Number of Slides: {n_slides}
        - Current Date and Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        - Additional Information: {additional_context or ""}
    """


def get_messages(
    content: str,
    n_slides: int,
    language: str,
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
):
    return [
        LLMSystemMessage(
            content=get_system_prompt(
                tone, verbosity, instructions, include_title_slide
            ),
        ),
        LLMUserMessage(
            content=get_user_prompt(content, n_slides, language, additional_context),
        ),
    ]


async def generate_ppt_outline(
    content: str,
    n_slides: int,
    language: Optional[str] = None,
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
    web_search: bool = False,
):
    model = get_model()
    response_model = get_presentation_outline_model_with_n_slides(n_slides)

    import asyncio, random
    from constants.llm import FALLBACK_GOOGLE_MODEL

    max_retries = 10
    fallback_after = 5
    current_model = model
    messages = get_messages(
        content, n_slides, language, additional_context,
        tone, verbosity, instructions, include_title_slide,
    )
    schema = response_model.model_json_schema()

    for attempt in range(max_retries):
        client = LLMClient()
        try:
            async for chunk in client.stream_structured(
                current_model,
                messages,
                schema,
                strict=True,
                tools=(
                    [SearchWebTool]
                    if (client.enable_web_grounding() and web_search)
                    else None
                ),
            ):
                yield chunk
            return  # Success - exit retry loop
        except Exception as e:
            error_msg = str(e).lower()
            # If fallback model is not available, revert to primary
            if "404" in error_msg and current_model == FALLBACK_GOOGLE_MODEL:
                print(f"Fallback model {FALLBACK_GOOGLE_MODEL} not available, reverting to {model}")
                current_model = model
            is_retryable = (
                "503" in error_msg or "429" in error_msg
                or "high demand" in error_msg or "service unavailable" in error_msg
                or "404" in error_msg
            )
            if is_retryable and attempt < max_retries - 1:
                if attempt + 1 >= fallback_after and current_model != FALLBACK_GOOGLE_MODEL and "404" not in error_msg:
                    print(f"Outline: switching to fallback model {FALLBACK_GOOGLE_MODEL} after {attempt + 1} failures")
                    current_model = FALLBACK_GOOGLE_MODEL
                wait_time = min(2 ** attempt + random.uniform(0, 1), 30)
                print(f"Outline generation failed (attempt {attempt + 1}/{max_retries}): retrying in {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
                continue
            yield handle_llm_client_exceptions(e)
            return
