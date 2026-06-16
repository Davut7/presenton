"""
Focused tests for the cool-down-based key rotator. Doesn't import the
heavy FastAPI/SQLModel stack — just unit-tests the rotation logic.

Run:
    cd presenton/servers/fastapi
    python3 -m pytest tests/test_key_rotator.py -v
"""
import os
import time
import sys
import unittest
from unittest.mock import patch

# Add fastapi root to path so we can import services.image_generation_service
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub heavy deps so we can unit-test the rotator without spinning up the
# entire FastAPI / Google / OpenAI stack.
import types
def _stub(name: str, **attrs):
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod

# Minimum surface area: just the symbols touched at import time.
class _StubHTTPException(Exception):
    def __init__(self, status_code=500, detail=""):
        self.status_code = status_code
        self.detail = detail
_stub("fastapi", HTTPException=_StubHTTPException)
class _GenAIClient:
    pass
_stub("google", genai=types.SimpleNamespace(Client=_GenAIClient))
_stub("google.genai", Client=_GenAIClient)
class _AsyncOpenAI:
    pass
_stub("openai", NOT_GIVEN=None, AsyncOpenAI=_AsyncOpenAI)
# Mirror app-relative imports the service does.
class _ImagePrompt:
    pass
_stub("models", )
_stub("models.image_prompt", ImagePrompt=_ImagePrompt)
class _ImageAsset:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
_stub("models.sql", )
_stub("models.sql.image_asset", ImageAsset=_ImageAsset)
_stub("utils", )
_stub("utils.get_env",
      get_dall_e_3_quality_env=lambda: None,
      get_gpt_image_1_5_quality_env=lambda: None,
      get_pexels_api_key_env=lambda: os.getenv("PEXELS_API_KEY"),
      get_pixabay_api_key_env=lambda: os.getenv("PIXABAY_API_KEY"),
      get_comfyui_url_env=lambda: None,
      get_comfyui_workflow_env=lambda: None,
      get_unsplash_access_key_env=lambda: os.getenv("UNSPLASH_ACCESS_KEY"))
_stub("utils.image_provider",
      is_gpt_image_1_5_selected=lambda: False,
      is_image_generation_disabled=lambda: False,
      is_pixels_selected=lambda: True,
      is_pixabay_selected=lambda: False,
      is_gemini_flash_selected=lambda: False,
      is_nanobanana_pro_selected=lambda: False,
      is_dalle3_selected=lambda: False,
      is_comfyui_selected=lambda: False)


class TestKeyRotator(unittest.TestCase):
    """_KeyRotator cool-down behaviour — the heart of the fix."""

    def _make_rotator(self, env_value: str):
        # Set env BEFORE constructing rotator (which reads it lazily on first
        # access). Restore on tearDown.
        os.environ["TEST_KEYS"] = env_value
        from services.image_generation_service import _KeyRotator
        r = _KeyRotator("TEST_KEYS", "TestProvider")
        # Force eager init so the env value is captured even if the test
        # later mutates os.environ for unrelated reasons.
        r._init_keys()
        return r

    def tearDown(self):
        os.environ.pop("TEST_KEYS", None)

    def test_empty_env_returns_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_KEYS", None)
            r = self._make_rotator("")
            self.assertIsNone(r.next_key())
            self.assertEqual(r.key_count(), 0)

    def test_single_key_round_robin(self):
        r = self._make_rotator("alpha")
        self.assertEqual(r.next_key(), "alpha")
        self.assertEqual(r.next_key(), "alpha")  # repeats — single key
        self.assertEqual(r.key_count(), 1)
        self.assertEqual(r.live_count(), 1)

    def test_multi_key_round_robin(self):
        r = self._make_rotator("a,b,c")
        # Round-robin order
        self.assertEqual(r.next_key(), "a")
        self.assertEqual(r.next_key(), "b")
        self.assertEqual(r.next_key(), "c")
        self.assertEqual(r.next_key(), "a")

    def test_dead_key_skipped(self):
        r = self._make_rotator("a,b,c")
        r.mark_rate_limited("b", seconds=60)
        # Cycle: a (live) → b dead skipped → c (live) → a → c → a ...
        seen = [r.next_key() for _ in range(6)]
        self.assertNotIn("b", seen)
        self.assertIn("a", seen)
        self.assertIn("c", seen)
        self.assertEqual(r.live_count(), 2)

    def test_all_dead_returns_none(self):
        r = self._make_rotator("a,b")
        r.mark_rate_limited("a", seconds=60)
        r.mark_rate_limited("b", seconds=60)
        # All keys cool-down'd — caller signal to fall back to alternative.
        self.assertIsNone(r.next_key())
        self.assertEqual(r.live_count(), 0)

    def test_cooldown_expires(self):
        r = self._make_rotator("a,b")
        # Cool-down 0.01s for a fast test.
        r.mark_rate_limited("a", seconds=1)
        # Manipulate the internal map to fake elapsed time without sleeping.
        r._dead_until["a"] = time.monotonic() - 1.0
        self.assertEqual(r.live_count(), 2)
        # Both keys should now be in rotation again.
        seen = set(r.next_key() for _ in range(4))
        self.assertEqual(seen, {"a", "b"})

    def test_whitespace_in_env_is_trimmed(self):
        r = self._make_rotator(" k1 , k2 ,k3")
        self.assertEqual(r.key_count(), 3)
        keys_seen = [r.next_key() for _ in range(3)]
        self.assertEqual(set(keys_seen), {"k1", "k2", "k3"})

    def test_empty_strings_filtered(self):
        r = self._make_rotator("a,,b,,,c,")
        self.assertEqual(r.key_count(), 3)


class TestPexelsConcurrencyResolution(unittest.TestCase):
    """_resolve_pexels_concurrent_cap should scale with key count."""

    def test_explicit_override_wins(self):
        with patch.dict(os.environ, {"PEXELS_MAX_CONCURRENT": "12", "PEXELS_API_KEY": "a,b"}):
            # Re-import to pick up env at module load.
            from importlib import reload
            from services import image_generation_service as svc
            reload(svc)
            self.assertEqual(svc._resolve_pexels_concurrent_cap(), 12)

    def test_scales_with_key_count(self):
        with patch.dict(os.environ, {"PEXELS_MAX_CONCURRENT": "0", "PEXELS_API_KEY": "a,b,c,d,e"}):
            from importlib import reload
            from services import image_generation_service as svc
            reload(svc)
            # 5 keys × 2 = 10
            self.assertEqual(svc._resolve_pexels_concurrent_cap(), 10)

    def test_floor_of_4(self):
        with patch.dict(os.environ, {"PEXELS_MAX_CONCURRENT": "0", "PEXELS_API_KEY": "only"}):
            from importlib import reload
            from services import image_generation_service as svc
            reload(svc)
            # 1 key × 2 = 2, but floor is 4
            self.assertEqual(svc._resolve_pexels_concurrent_cap(), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
