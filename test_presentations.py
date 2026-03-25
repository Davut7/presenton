#!/usr/bin/env python3
"""
Test script: creates N presentations via the presenton API and checks
whether slides contain real images (not placeholders / missing).
"""

import asyncio
import json
import time
import aiohttp

BASE_URL = "http://localhost:5050"
NUM_PRESENTATIONS = 100
NUM_SLIDES = 10

TOPICS = [
    "Букеты и их виды",
    "Гонки и их виды",
    "Искусственный интеллект в медицине",
    "Космические технологии будущего",
    "История кинематографа",
    "Мировые океаны и экосистемы",
    "Современная архитектура",
    "Электромобили и будущее транспорта",
    "Психология цвета в дизайне",
    "Робототехника в промышленности",
]

PLACEHOLDER_MARKERS = [
    "placeholder",
    "/static/images/placeholder",
    "/static/icons/placeholder",
]


def is_real_image(url: str) -> bool:
    if not url:
        return False
    for marker in PLACEHOLDER_MARKERS:
        if marker in url:
            return False
    return True


def count_images_in_content(content: dict, key="__image_url__") -> tuple[int, int]:
    """Returns (total_image_slots, real_images)"""
    total = 0
    real = 0

    def walk(obj):
        nonlocal total, real
        if isinstance(obj, dict):
            if key in obj:
                total += 1
                if is_real_image(obj[key]):
                    real += 1
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(content)
    return total, real


async def create_presentation(session: aiohttp.ClientSession, topic: str, idx: int):
    result = {
        "idx": idx,
        "topic": topic,
        "status": "error",
        "total_slides": 0,
        "slides_with_image_slots": 0,
        "total_image_slots": 0,
        "real_images": 0,
        "placeholder_images": 0,
        "error": None,
    }

    try:
        # Use /generate endpoint (slides are saved even if PPTX export fails)
        gen_resp = await session.post(
            f"{BASE_URL}/api/v1/ppt/presentation/generate",
            json={
                "content": topic,
                "n_slides": NUM_SLIDES,
                "language": "Russian",
                "template": "general",
                "web_search": False,
                "include_title_slide": True,
                "include_table_of_contents": False,
                "export_as": "pptx",
            },
            timeout=aiohttp.ClientTimeout(total=600),
        )

        # Even if export fails (500), slides may be saved. Try to find them.
        gen_pres_id = None
        if gen_resp.status == 200:
            gen_result = await gen_resp.json()
            gen_pres_id = gen_result.get("edit_path", "").replace("/presentation?id=", "")
        else:
            # Try to find the latest presentation for this topic
            all_resp = await session.get(
                f"{BASE_URL}/api/v1/ppt/presentation/all",
                timeout=aiohttp.ClientTimeout(total=30),
            )
            if all_resp.status == 200:
                all_pres = await all_resp.json()
                for p in all_pres:
                    if p.get("content") == topic:
                        gen_pres_id = p["id"]
                        break

        if not gen_pres_id:
            err_text = await gen_resp.text()
            result["error"] = f"Generate failed: {gen_resp.status} - {err_text[:200]}"
            return result

        # Fetch the generated presentation with slides
        fetch_resp = await session.get(
            f"{BASE_URL}/api/v1/ppt/presentation/{gen_pres_id}",
            timeout=aiohttp.ClientTimeout(total=30),
        )
        if fetch_resp.status != 200:
            result["error"] = f"Fetch failed: {fetch_resp.status}"
            return result

        presentation = await fetch_resp.json()
        slides = presentation.get("slides", [])
        result["total_slides"] = len(slides)

        total_image_slots = 0
        total_real_images = 0

        for slide in slides:
            content = slide.get("content", {})
            slots, real = count_images_in_content(content)
            total_image_slots += slots
            total_real_images += real
            if slots > 0:
                result["slides_with_image_slots"] += 1

        result["total_image_slots"] = total_image_slots
        result["real_images"] = total_real_images
        result["placeholder_images"] = total_image_slots - total_real_images
        result["status"] = "ok"

    except asyncio.TimeoutError:
        result["error"] = "Timeout"
    except Exception as e:
        result["error"] = str(e)[:200]

    return result


async def main():
    print(f"=== Testing {NUM_PRESENTATIONS} presentations x {NUM_SLIDES} slides ===\n")

    connector = aiohttp.TCPConnector(limit=2)  # max 2 concurrent to avoid overload
    async with aiohttp.ClientSession(connector=connector) as session:
        # Run sequentially to avoid rate limits
        results = []
        for i in range(NUM_PRESENTATIONS):
            topic = TOPICS[i % len(TOPICS)]
            print(f"[{i+1}/{NUM_PRESENTATIONS}] Creating: {topic} ...", flush=True)
            start = time.time()
            r = await create_presentation(session, topic, i)
            elapsed = time.time() - start
            results.append(r)

            status_icon = "OK" if r["status"] == "ok" else "FAIL"
            print(
                f"  {status_icon} | slides={r['total_slides']} | "
                f"image_slots={r['total_image_slots']} | "
                f"real={r['real_images']} | placeholder={r['placeholder_images']} | "
                f"{elapsed:.1f}s"
                + (f" | err={r['error']}" if r["error"] else "")
            )

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    ok = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] != "ok"]
    defective = [r for r in ok if r["real_images"] == 0 and r["total_image_slots"] > 0]
    partial = [r for r in ok if 0 < r["real_images"] < r["total_image_slots"]]
    perfect = [r for r in ok if r["real_images"] == r["total_image_slots"] and r["total_image_slots"] > 0]
    no_slots = [r for r in ok if r["total_image_slots"] == 0]

    total_slots = sum(r["total_image_slots"] for r in ok)
    total_real = sum(r["real_images"] for r in ok)

    print(f"Total presentations:    {len(results)}")
    print(f"  Successful:           {len(ok)}")
    print(f"  Failed (API error):   {len(failed)}")
    print(f"  Perfect (all images): {len(perfect)}")
    print(f"  Partial images:       {len(partial)}")
    print(f"  NO images (defective):{len(defective)}")
    print(f"  No image slots:       {len(no_slots)}")
    print(f"\nTotal image slots:      {total_slots}")
    print(f"Real images:            {total_real}")
    print(f"Placeholders:           {total_slots - total_real}")
    if total_slots > 0:
        print(f"Image success rate:     {total_real/total_slots*100:.1f}%")

    if failed:
        print("\nFailed presentations:")
        for r in failed:
            print(f"  [{r['idx']}] {r['topic']}: {r['error']}")

    if defective:
        print("\nDefective (no images):")
        for r in defective:
            print(f"  [{r['idx']}] {r['topic']}: {r['total_image_slots']} slots, 0 real images")


if __name__ == "__main__":
    asyncio.run(main())
