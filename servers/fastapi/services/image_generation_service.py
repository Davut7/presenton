import asyncio
import base64
import json
import os
from urllib.parse import quote_plus
import aiohttp
from fastapi import HTTPException
from google import genai
from openai import NOT_GIVEN, AsyncOpenAI
from models.image_prompt import ImagePrompt
from models.sql.image_asset import ImageAsset
from utils.get_env import (
    get_dall_e_3_quality_env,
    get_gpt_image_1_5_quality_env,
    get_pexels_api_key_env,
)
from utils.get_env import get_pixabay_api_key_env
from utils.get_env import get_comfyui_url_env
from utils.get_env import get_comfyui_workflow_env
from utils.get_env import get_unsplash_access_key_env
import time
from utils.image_provider import (
    is_gpt_image_1_5_selected,
    is_image_generation_disabled,
    is_pixels_selected,
    is_pixabay_selected,
    is_gemini_flash_selected,
    is_nanobanana_pro_selected,
    is_dalle3_selected,
    is_comfyui_selected,
)
import uuid


_PEXELS_MAX_RETRIES = int(os.getenv("PEXELS_MAX_RETRIES", "10"))
_PEXELS_TIMEOUT = int(os.getenv("PEXELS_TIMEOUT", "60"))
_PEXELS_MAX_CONCURRENT = int(os.getenv("PEXELS_MAX_CONCURRENT", "0") or 0)
# Per-key cool-down when Pexels returns 429. Pexels resets hourly buckets,
# so 60s is a decent middle ground: long enough to clear small bursts,
# short enough to recover quickly if rate-limit was transient.
_PEXELS_KEY_COOLDOWN_SECONDS = int(os.getenv("PEXELS_KEY_COOLDOWN_SECONDS", "60"))


class _KeyRotator:
    """
    Round-robin rotator with per-key cool-down. When a key gets a 429,
    mark it dead for COOLDOWN seconds; skip it in future picks. This is
    materially better than blind round-robin: with 5 keys where 1 is
    rate-limited, blind RR loses 20% of requests to instant retries
    while cool-down RR routes 100% of traffic through the 4 live keys.

    Used for both Pexels and Unsplash (each gets its own instance).
    """

    def __init__(self, env_var: str, label: str):
        self._env_var = env_var
        self._label = label
        self._keys: list[str] = []
        self._index: int = 0
        self._dead_until: dict[str, float] = {}
        self._inited = False

    def _init_keys(self):
        raw = os.getenv(self._env_var) or ""
        self._keys = [k.strip() for k in raw.split(",") if k.strip()]
        self._inited = True

    def key_count(self) -> int:
        if not self._inited:
            self._init_keys()
        return len(self._keys)

    def live_count(self) -> int:
        """How many keys are NOT currently in cool-down."""
        if not self._inited:
            self._init_keys()
        now = time.monotonic()
        return sum(1 for k in self._keys if self._dead_until.get(k, 0) <= now)

    def next_key(self) -> str | None:
        """Return a live key, or None if all keys are in cool-down."""
        if not self._inited:
            self._init_keys()
        if not self._keys:
            return None
        now = time.monotonic()
        for _ in range(len(self._keys)):
            key = self._keys[self._index % len(self._keys)]
            self._index += 1
            if self._dead_until.get(key, 0) <= now:
                return key
        return None

    def mark_rate_limited(self, key: str, seconds: int = _PEXELS_KEY_COOLDOWN_SECONDS):
        """Mark a key dead for `seconds`. Idempotent — extends cool-down."""
        self._dead_until[key] = time.monotonic() + seconds
        print(f"[{self._label}] key …{key[-6:]} cooled down for {seconds}s "
              f"(live: {self.live_count()}/{self.key_count()})")


# Global rotators, one per provider. Pixabay and Unsplash are fallback
# providers used by get_image_from_pexels when Pexels exhausts its keys.
# Either can be configured (comma-separated) or both — order is
# Pixabay → Unsplash. If neither set, fallback is a no-op.
_pexels_rotator = _KeyRotator("PEXELS_API_KEY", "Pexels")
_pixabay_rotator = _KeyRotator("PIXABAY_API_KEY", "Pixabay")
_unsplash_rotator = _KeyRotator("UNSPLASH_ACCESS_KEY", "Unsplash")


def _resolve_pexels_concurrent_cap() -> int:
    """
    Concurrency cap = explicit env override OR 2 × live key count (min 4).
    With 5 keys we allow ~10 concurrent Pexels calls; with 1 key we stick
    to 4 (the old default). This lets you horizontally scale by just
    adding more keys to PEXELS_API_KEY.
    """
    if _PEXELS_MAX_CONCURRENT > 0:
        return _PEXELS_MAX_CONCURRENT
    keys = max(1, _pexels_rotator.key_count())
    return max(4, keys * 2)


# Initialised lazily on first use so that env vars set after import still apply.
_pexels_semaphore: asyncio.Semaphore | None = None


def _get_pexels_semaphore() -> asyncio.Semaphore:
    global _pexels_semaphore
    if _pexels_semaphore is None:
        _pexels_semaphore = asyncio.Semaphore(_resolve_pexels_concurrent_cap())
    return _pexels_semaphore


# Back-compat alias for the old class name used in tests.
class _PexelsKeyRotator:  # noqa: N801
    """Deprecated shim — kept for tests that reference the old name."""
    @classmethod
    def init_keys(cls):
        _pexels_rotator._init_keys()

    @classmethod
    def next_key(cls) -> str | None:
        return _pexels_rotator.next_key()

    @classmethod
    def key_count(cls) -> int:
        return _pexels_rotator.key_count()


class ImageGenerationService:
    def __init__(self, output_directory: str):
        self.output_directory = output_directory
        self.is_image_generation_disabled = is_image_generation_disabled()
        self.image_gen_func = self.get_image_gen_func()

    def get_image_gen_func(self):
        if self.is_image_generation_disabled:
            return None

        if is_pixabay_selected():
            return self.get_image_from_pixabay
        elif is_pixels_selected():
            return self.get_image_from_pexels
        elif is_gemini_flash_selected():
            return self.generate_image_gemini_flash
        elif is_nanobanana_pro_selected():
            return self.generate_image_nanobanana_pro
        elif is_dalle3_selected():
            return self.generate_image_openai_dalle3
        elif is_gpt_image_1_5_selected():
            return self.generate_image_openai_gpt_image_1_5
        elif is_comfyui_selected():
            return self.generate_image_comfyui
        return None

    def is_stock_provider_selected(self):
        return is_pixels_selected() or is_pixabay_selected()

    async def generate_image(self, prompt: ImagePrompt) -> str | ImageAsset:
        """
        Generates an image based on the provided prompt.
        - If no image generation function is available, returns a placeholder image.
        - If the stock provider is selected, it uses the prompt directly,
        otherwise it uses the full image prompt with theme.
        - Output Directory is used for saving the generated image not the stock provider.
        """
        if self.is_image_generation_disabled:
            print("Image generation is disabled. Using placeholder image.")
            return "/static/images/placeholder.jpg"

        if not self.image_gen_func:
            print("No image generation function found. Using placeholder image.")
            return "/static/images/placeholder.jpg"

        image_prompt = prompt.get_image_prompt(
            with_theme=not self.is_stock_provider_selected()
        )
        print(f"Request - Generating Image for {image_prompt}")

        try:
            if self.is_stock_provider_selected():
                image_path = await self.image_gen_func(image_prompt)
            else:
                image_path = await self.image_gen_func(
                    image_prompt, self.output_directory
                )
            if image_path:
                if image_path.startswith("http"):
                    return image_path
                elif os.path.exists(image_path):
                    return ImageAsset(
                        path=image_path,
                        is_uploaded=False,
                        extras={
                            "prompt": prompt.prompt,
                            "theme_prompt": prompt.theme_prompt,
                        },
                    )
            raise Exception(f"Image not found at {image_path}")

        except Exception as e:
            print(f"Error generating image: {e}")
            return "/static/images/placeholder.jpg"

    async def generate_image_openai(
        self, prompt: str, output_directory: str, model: str, quality: str
    ) -> str:
        client = AsyncOpenAI()
        result = await client.images.generate(
            model=model,
            prompt=prompt,
            n=1,
            quality=quality,
            response_format="b64_json" if model == "dall-e-3" else NOT_GIVEN,
            size="1024x1024",
        )
        image_path = os.path.join(output_directory, f"{uuid.uuid4()}.png")
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(result.data[0].b64_json))
        return image_path

    async def generate_image_openai_dalle3(
        self, prompt: str, output_directory: str
    ) -> str:
        return await self.generate_image_openai(
            prompt,
            output_directory,
            "dall-e-3",
            get_dall_e_3_quality_env() or "standard",
        )

    async def generate_image_openai_gpt_image_1_5(
        self, prompt: str, output_directory: str
    ) -> str:
        return await self.generate_image_openai(
            prompt,
            output_directory,
            "gpt-image-1.5",
            get_gpt_image_1_5_quality_env() or "medium",
        )

    async def _generate_image_google(
        self, prompt: str, output_directory: str, model: str
    ) -> str:
        """Base method for Google image generation models."""
        client = genai.Client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[prompt],
        )

        image_path = None
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image = part.as_image()
                image_path = os.path.join(output_directory, f"{uuid.uuid4()}.jpg")
                image.save(image_path)

        if not image_path:
            raise HTTPException(
                status_code=500, detail=f"No image generated by google {model}"
            )

        return image_path

    async def generate_image_gemini_flash(
        self, prompt: str, output_directory: str
    ) -> str:
        """Generate image using Gemini Flash (gemini-2.5-flash-image-preview)."""
        return await self._generate_image_google(
            prompt, output_directory, "gemini-2.5-flash-image-preview"
        )

    async def generate_image_nanobanana_pro(
        self, prompt: str, output_directory: str
    ) -> str:
        """Generate image using NanoBanana Pro (gemini-3-pro-image-preview)."""
        return await self._generate_image_google(
            prompt, output_directory, "gemini-3-pro-image-preview"
        )

    async def get_image_from_pexels(self, prompt: str) -> str:
        """
        Pexels with cool-down rotation + Unsplash fallback.

        Strategy:
          1. Try Pexels with a live (not-cool-down'd) key.
          2. On 429, mark that key dead for COOLDOWN seconds and retry
             with the next live key — no sleep, immediate switch.
          3. If all Pexels keys are dead → break and try Unsplash.
          4. If Unsplash also fails → raise (caller will return placeholder).
        """
        encoded_prompt = quote_plus(prompt)
        async with _get_pexels_semaphore():
            for attempt in range(_PEXELS_MAX_RETRIES):
                api_key = _pexels_rotator.next_key()
                if not api_key:
                    # All Pexels keys are cool-down'd OR no keys configured.
                    print(
                        f"[Pexels] no live keys (configured={_pexels_rotator.key_count()}, "
                        f"live={_pexels_rotator.live_count()}) — trying Unsplash"
                    )
                    break
                try:
                    async with aiohttp.ClientSession(trust_env=True) as session:
                        response = await session.get(
                            f"https://api.pexels.com/v1/search?query={encoded_prompt}&per_page=5",
                            headers={"Authorization": api_key},
                            timeout=aiohttp.ClientTimeout(total=_PEXELS_TIMEOUT),
                        )
                        if response.status == 429:
                            _pexels_rotator.mark_rate_limited(api_key)
                            # Immediately try the next live key — no sleep
                            # needed because we won't pick the same dead key.
                            continue
                        if response.status != 200:
                            raise Exception(f"Pexels API returned status {response.status}")
                        data = await response.json()
                        photos = data.get("photos", [])
                        if not photos:
                            # Empty results aren't a rate-limit issue — fall
                            # straight to Unsplash for a different photo pool.
                            print(f"[Pexels] no photos for '{prompt[:60]}' — trying Unsplash")
                            break
                        return photos[0]["src"]["large"]
                except asyncio.TimeoutError:
                    # Network timeout — treat as a transient failure and try
                    # the next key. Don't cool-down (the key may be fine).
                    print(f"[Pexels] timeout on attempt {attempt + 1} — retrying")
                    continue

            # Fallback chain: Pixabay → Unsplash. First one that finds an
            # image wins. If both fail or aren't configured, raise so caller
            # produces a placeholder.
            fallback_url = await self._try_fallback_providers(encoded_prompt, prompt)
            if fallback_url:
                return fallback_url
            raise Exception(
                f"Pexels exhausted after {_PEXELS_MAX_RETRIES} attempts and all fallback providers failed"
            )

    async def _try_fallback_providers(self, encoded_prompt: str, original_prompt: str) -> str | None:
        """
        Try fallback image providers in priority order: Pixabay, then
        Unsplash. Each provider supports key rotation with cool-down. The
        first successful URL wins; failures are silently swallowed so the
        caller can fall through to a placeholder if everything dies.

        Why Pixabay first: free tier is 5000 req/h vs Unsplash demo 50/h
        — much higher headroom on the same workload.
        """
        url = await self._try_pixabay_fallback(encoded_prompt, original_prompt)
        if url:
            return url
        return await self._try_unsplash_fallback(encoded_prompt, original_prompt)

    async def _try_pixabay_fallback(self, encoded_prompt: str, original_prompt: str) -> str | None:
        """Pixabay fallback. Same rotation + cool-down pattern as Pexels."""
        if _pixabay_rotator.key_count() == 0:
            return None

        for attempt in range(3):
            api_key = _pixabay_rotator.next_key()
            if not api_key:
                print("[Pixabay] no live keys left, skipping")
                return None
            try:
                async with aiohttp.ClientSession(trust_env=True) as session:
                    # Pixabay auth is via ?key=... query param (no header).
                    response = await session.get(
                        f"https://pixabay.com/api/?key={api_key}&q={encoded_prompt}&image_type=photo&per_page=5",
                        timeout=aiohttp.ClientTimeout(total=_PEXELS_TIMEOUT),
                    )
                    if response.status == 429:
                        # Pixabay: 100 req/min, headers indicate reset.
                        # We cool-down 60s — same as other providers.
                        _pixabay_rotator.mark_rate_limited(api_key)
                        continue
                    if response.status != 200:
                        print(f"[Pixabay] HTTP {response.status} — skipping")
                        return None
                    data = await response.json()
                    hits = data.get("hits", [])
                    if not hits:
                        print(f"[Pixabay] no results for '{original_prompt[:60]}'")
                        return None
                    return hits[0]["largeImageURL"]
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"[Pixabay] unexpected error: {e}")
                return None
        return None

    async def _try_unsplash_fallback(self, encoded_prompt: str, original_prompt: str) -> str | None:
        """Unsplash fallback (lowest priority — used only if Pixabay also empty)."""
        if _unsplash_rotator.key_count() == 0:
            return None

        for attempt in range(3):
            access_key = _unsplash_rotator.next_key()
            if not access_key:
                print("[Unsplash] no live keys left, skipping")
                return None
            try:
                async with aiohttp.ClientSession(trust_env=True) as session:
                    response = await session.get(
                        f"https://api.unsplash.com/search/photos?query={encoded_prompt}&per_page=5",
                        headers={"Authorization": f"Client-ID {access_key}"},
                        timeout=aiohttp.ClientTimeout(total=_PEXELS_TIMEOUT),
                    )
                    if response.status == 429:
                        _unsplash_rotator.mark_rate_limited(access_key)
                        continue
                    if response.status != 200:
                        print(f"[Unsplash] HTTP {response.status} — skipping")
                        return None
                    data = await response.json()
                    results = data.get("results", [])
                    if not results:
                        print(f"[Unsplash] no results for '{original_prompt[:60]}'")
                        return None
                    return results[0]["urls"]["regular"]
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"[Unsplash] unexpected error: {e}")
                return None
        return None

    async def get_image_from_pixabay(self, prompt: str) -> str:
        encoded_prompt = quote_plus(prompt)
        async with aiohttp.ClientSession(trust_env=True) as session:
            response = await session.get(
                f"https://pixabay.com/api/?key={get_pixabay_api_key_env()}&q={encoded_prompt}&image_type=photo&per_page=5",
                timeout=aiohttp.ClientTimeout(total=30),
            )
            if response.status != 200:
                raise Exception(f"Pixabay API returned status {response.status}")
            data = await response.json()
            hits = data.get("hits", [])
            if not hits:
                raise Exception(f"Pixabay returned no images for query: {prompt}")
            image_url = hits[0]["largeImageURL"]
            return image_url

    async def generate_image_comfyui(self, prompt: str, output_directory: str) -> str:
        """
        Generate image using ComfyUI workflow API.

        User provides:
        - COMFYUI_URL: ComfyUI server URL (e.g., http://192.168.1.7:8188)
        - COMFYUI_WORKFLOW: Workflow JSON exported from ComfyUI

        The workflow should have a CLIPTextEncode node with "Positive" in the title
        where the prompt will be injected.

        Args:
            prompt: The text prompt for image generation
            output_directory: Directory to save the generated image

        Returns:
            Path to the generated image file
        """
        comfyui_url = get_comfyui_url_env()
        workflow_json = get_comfyui_workflow_env()

        if not comfyui_url:
            raise ValueError("COMFYUI_URL environment variable is not set")

        if not workflow_json:
            raise ValueError(
                "COMFYUI_WORKFLOW environment variable is not set. Please provide a ComfyUI workflow JSON."
            )

        # Ensure URL doesn't have trailing slash
        comfyui_url = comfyui_url.rstrip("/")

        # Parse the workflow JSON
        try:
            workflow = json.loads(workflow_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid workflow JSON: {str(e)}")

        # Find and update the positive prompt node
        workflow = self._inject_prompt_into_workflow(workflow, prompt)

        async with aiohttp.ClientSession(trust_env=True) as session:
            # Step 1: Submit workflow
            prompt_id = await self._submit_comfyui_workflow(
                session, comfyui_url, workflow
            )

            # Step 2: Wait for completion
            status_data = await self._wait_for_comfyui_completion(
                session, comfyui_url, prompt_id
            )

            # Step 3: Download the generated image
            image_path = await self._download_comfyui_image(
                session, comfyui_url, status_data, prompt_id, output_directory
            )

            return image_path

    def _inject_prompt_into_workflow(self, workflow: dict, prompt: str) -> dict:
        """
        Find the prompt node in the workflow and inject the prompt text.
        Looks for a node with title 'Input Prompt' (case-insensitive).

        User must rename their prompt node to 'Input Prompt' in ComfyUI.
        """
        for node_id, node_data in workflow.items():
            meta = node_data.get("_meta", {})
            title = meta.get("title", "").lower()

            if title == "input prompt":
                if "inputs" in node_data and "text" in node_data["inputs"]:
                    node_data["inputs"]["text"] = prompt
                    print(
                        f"Injected prompt into node {node_id}: {meta.get('title', '')}"
                    )
                    return workflow

        raise ValueError(
            "Could not find a node with title 'Input Prompt' in the workflow. Please rename your prompt node to 'Input Prompt' in ComfyUI."
        )

    async def _submit_comfyui_workflow(
        self, session: aiohttp.ClientSession, comfyui_url: str, workflow: dict
    ) -> str:
        """Submit workflow to ComfyUI and return the prompt_id."""
        client_id = str(uuid.uuid4())
        payload = {"prompt": workflow, "client_id": client_id}

        response = await session.post(
            f"{comfyui_url}/prompt",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        )

        if response.status != 200:
            error_text = await response.text()
            raise Exception(f"Failed to submit workflow to ComfyUI: {error_text}")

        data = await response.json()
        prompt_id = data.get("prompt_id")

        if not prompt_id:
            raise Exception("No prompt_id returned from ComfyUI")

        print(f"ComfyUI workflow submitted. Prompt ID: {prompt_id}")
        return prompt_id

    async def _wait_for_comfyui_completion(
        self,
        session: aiohttp.ClientSession,
        comfyui_url: str,
        prompt_id: str,
        timeout: int = 300,
        poll_interval: int = 4,
    ) -> dict:
        """Poll ComfyUI history endpoint until workflow completes."""
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise Exception(f"ComfyUI workflow timed out after {timeout} seconds")

            await asyncio.sleep(poll_interval)

            response = await session.get(
                f"{comfyui_url}/history/{prompt_id}",
                timeout=aiohttp.ClientTimeout(total=30),
            )

            if response.status != 200:
                continue

            try:
                status_data = await response.json()
            except Exception as _:
                continue

            if prompt_id in status_data:
                execution_data = status_data[prompt_id]

                # Check for completion
                if "status" in execution_data:
                    status = execution_data["status"]
                    if status.get("completed", False):
                        print("ComfyUI workflow completed successfully")
                        return status_data
                    if "error" in status:
                        raise Exception(f"ComfyUI workflow error: {status['error']}")

                # Also check if outputs exist (alternative completion check)
                if "outputs" in execution_data and execution_data["outputs"]:
                    print("ComfyUI workflow completed (outputs found)")
                    return status_data

            print(f"Waiting for ComfyUI workflow... ({int(elapsed)}s)")

    async def _download_comfyui_image(
        self,
        session: aiohttp.ClientSession,
        comfyui_url: str,
        status_data: dict,
        prompt_id: str,
        output_directory: str,
    ) -> str:
        """Download the generated image from ComfyUI."""
        if prompt_id not in status_data:
            raise Exception("Prompt ID not found in status data")

        outputs = status_data[prompt_id].get("outputs", {})

        if not outputs:
            raise Exception("No outputs found in ComfyUI response")

        # Find the first image in outputs
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                for image_info in node_output["images"]:
                    filename = image_info["filename"]
                    subfolder = image_info.get("subfolder", "")

                    # Build view params
                    params = {"filename": filename, "type": "output"}
                    if subfolder:
                        params["subfolder"] = subfolder

                    # Download the image
                    response = await session.get(
                        f"{comfyui_url}/view",
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=60),
                    )

                    if response.status == 200:
                        image_data = await response.read()

                        # Determine extension
                        ext = filename.split(".")[-1] if "." in filename else "png"
                        image_path = os.path.join(
                            output_directory, f"{uuid.uuid4()}.{ext}"
                        )

                        with open(image_path, "wb") as f:
                            f.write(image_data)

                        print(f"Downloaded image from ComfyUI: {image_path}")
                        return image_path
                    else:
                        raise Exception(f"Failed to download image: {response.status}")

        raise Exception("No images found in ComfyUI outputs")
