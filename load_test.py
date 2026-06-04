#!/usr/bin/env python3
"""
Load test for Presenton: fire N presentation generations concurrently and
measure success rate, latency, and failures.

Usage:
    python3 load_test.py \
        --base-url http://localhost:5000 \
        --api-key HL2Up5HS \
        --total 100 \
        --concurrent 10

Run this from the same host as Presenton (or from anywhere reachable).
"""
import argparse
import asyncio
import json
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

try:
    import aiohttp
except ImportError:
    print("Run: pip install aiohttp")
    raise SystemExit(1)


# 100 diverse topics so Gemini doesn't return cached responses
TOPICS = [
    "История развития искусственного интеллекта",
    "Климатические изменения и их последствия",
    "Квантовые компьютеры: принципы работы",
    "Эволюция языков программирования",
    "Биотехнологии в медицине",
    "Перспективы освоения Марса",
    "Возобновляемые источники энергии",
    "Цифровая трансформация бизнеса",
    "Кибербезопасность в современном мире",
    "Генетика и наследственность",
    "Архитектура микросервисов",
    "Развитие электротранспорта",
    "Психология лидерства",
    "Криптовалюты и блокчейн",
    "Эволюция социальных сетей",
    "Технологии 5G",
    "Промышленный интернет вещей",
    "Робототехника в производстве",
    "Машинное обучение в финансах",
    "Виртуальная и дополненная реальность",
    "История Древнего Египта",
    "Великая Отечественная война",
    "Промышленная революция в Англии",
    "Эпоха Возрождения",
    "Открытие Америки Колумбом",
    "Развитие демократии в Греции",
    "Холодная война: причины и итоги",
    "Распад Советского Союза",
    "Французская революция 1789 года",
    "Столетняя война",
    "Космическая гонка СССР и США",
    "Изобретение печатного станка",
    "Война за независимость США",
    "Реформы Петра I",
    "Падение Римской империи",
    "История олимпийских игр",
    "Открытие Антарктиды",
    "Развитие железных дорог",
    "Изобретение интернета",
    "История кино XX века",
    "Здоровый образ жизни",
    "Психическое здоровье и стресс",
    "Йога и медитация",
    "Правильное питание",
    "Физическая активность и долголетие",
    "Сон и его значение",
    "Профилактика заболеваний",
    "Вакцинация: мифы и факты",
    "Влияние смартфонов на здоровье",
    "Когнитивная психология",
    "Финансовая грамотность",
    "Инвестиции в фондовый рынок",
    "Управление личным бюджетом",
    "Налоговая система",
    "Кредитование физических лиц",
    "Страхование жизни",
    "Пенсионные накопления",
    "Стартапы и венчурное финансирование",
    "Маркетинг в социальных сетях",
    "Бренд-стратегия компании",
    "Океанические течения",
    "Вулканы и землетрясения",
    "Биоразнообразие тропических лесов",
    "Полярные сияния",
    "Эволюция Вселенной",
    "Чёрные дыры",
    "Солнечная система",
    "Жизнь в океанских глубинах",
    "Экологические катастрофы XX века",
    "Возобновляемые ресурсы Земли",
    "Микропластик в океане",
    "Таяние ледников",
    "Миграция животных",
    "Гены и эволюция",
    "Эпигенетика и наследование",
    "Архитектура мозга человека",
    "Память и обучение",
    "Эмоциональный интеллект",
    "Творчество и креативность",
    "Когнитивные искажения",
    "Лингвистика и происхождение языка",
    "Музыка и её влияние на мозг",
    "Архитектура XX века",
    "Современная живопись",
    "Театр и драматургия",
    "Балет: история и развитие",
    "Литература Серебряного века",
    "Импрессионизм в живописи",
    "Скульптура эпохи Возрождения",
    "Японское искусство гравюры",
    "Африканская традиционная музыка",
    "Фотография как искусство",
    "Кулинарные традиции мира",
    "Виноделие во Франции",
    "История чая",
    "Кофе: от зерна до чашки",
    "Уличная еда разных стран",
    "Ферментация продуктов",
    "Гастрономия молекулярной кухни",
    "Сыроделие в Европе",
    "Специи и пряности Востока",
    "Десерты Франции и Италии",
]


@dataclass
class Result:
    topic: str
    presentation_id: Optional[str] = None
    enqueued_at: float = 0.0
    completed_at: float = 0.0
    status: str = "pending"
    error: Optional[str] = None

    @property
    def duration(self) -> float:
        if self.completed_at and self.enqueued_at:
            return self.completed_at - self.enqueued_at
        return 0.0


async def submit_presentation(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    topic: str,
    sem: asyncio.Semaphore,
    results: list[Result],
) -> Result:
    result = Result(topic=topic)
    async with sem:
        result.enqueued_at = time.time()
        try:
            async with session.post(
                f"{base_url}/api/v1/ppt/presentation/generate/async",
                headers={"X-API-Key": api_key, "Authorization": f"Bearer {api_key}"},
                json={
                    "content": topic,
                    "n_slides": 7,
                    "language": "Russian",
                    "tone": "professional",
                    "verbosity": "standard",
                    "include_table_of_contents": False,
                    "include_title_slide": True,
                    "web_search": False,
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                body = await r.text()
                if r.status != 200:
                    result.status = "submit_failed"
                    result.error = f"HTTP {r.status}: {body[:200]}"
                    return result
                try:
                    data = json.loads(body)
                    result.presentation_id = data.get("presentation_id") or data.get("id")
                except Exception:
                    pass
        except Exception as e:
            result.status = "submit_exception"
            result.error = str(e)
            return result

        if not result.presentation_id:
            result.status = "no_id_returned"
            result.error = "No presentation_id in response"
            return result

        # Poll for completion
        deadline = time.time() + 600  # 10 min max per presentation
        while time.time() < deadline:
            try:
                async with session.get(
                    f"{base_url}/api/v1/ppt/presentation/{result.presentation_id}",
                    headers={"X-API-Key": api_key, "Authorization": f"Bearer {api_key}"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("slides") and len(data.get("slides", [])) > 0:
                            result.completed_at = time.time()
                            result.status = "success"
                            return result
            except Exception as e:
                result.error = str(e)
            await asyncio.sleep(5)

        result.status = "timeout"
        result.completed_at = time.time()
        return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--concurrent", type=int, default=10)
    parser.add_argument("--report", default="loadtest_report.json")
    args = parser.parse_args()

    topics = random.sample(TOPICS, min(args.total, len(TOPICS)))
    if args.total > len(TOPICS):
        topics += random.choices(TOPICS, k=args.total - len(TOPICS))

    print(f"Submitting {args.total} presentations with concurrency={args.concurrent}")
    print(f"Target: {args.base_url}")
    print("-" * 70)

    sem = asyncio.Semaphore(args.concurrent)
    results: list[Result] = []

    start = time.time()
    async with aiohttp.ClientSession() as session:
        tasks = [
            submit_presentation(session, args.base_url, args.api_key, topic, sem, results)
            for topic in topics
        ]

        completed = 0
        for fut in asyncio.as_completed(tasks):
            r = await fut
            results.append(r)
            completed += 1
            mark = "✅" if r.status == "success" else "❌"
            print(
                f"[{completed:>3}/{args.total}] {mark} {r.status:<20} "
                f"{r.duration:>6.1f}s  {r.topic[:50]}"
            )

    total_time = time.time() - start

    # ---- Summary ----
    print("\n" + "=" * 70)
    print(f"TOTAL TIME: {total_time:.1f}s ({total_time/60:.1f} min)")
    print(f"THROUGHPUT: {len(results)/total_time:.2f} presentations/sec")

    status_counts = Counter(r.status for r in results)
    print("\nSTATUS BREAKDOWN:")
    for status, count in status_counts.most_common():
        print(f"  {status:<25} {count:>4}")

    success_durations = [r.duration for r in results if r.status == "success"]
    if success_durations:
        avg = sum(success_durations) / len(success_durations)
        p50 = sorted(success_durations)[len(success_durations) // 2]
        p95 = sorted(success_durations)[int(len(success_durations) * 0.95)]
        p99 = sorted(success_durations)[int(len(success_durations) * 0.99)]
        print(f"\nSUCCESS LATENCY (n={len(success_durations)}):")
        print(f"  avg: {avg:.1f}s")
        print(f"  p50: {p50:.1f}s  |  p95: {p95:.1f}s  |  p99: {p99:.1f}s")
        print(f"  min: {min(success_durations):.1f}s  |  max: {max(success_durations):.1f}s")

    errors = [r for r in results if r.status != "success"]
    if errors:
        print(f"\nERRORS ({len(errors)}):")
        err_groups = Counter()
        for e in errors:
            key = (e.status, (e.error or "")[:80])
            err_groups[key] += 1
        for (status, error_snip), count in err_groups.most_common(10):
            print(f"  {count:>4}x {status}: {error_snip}")

    # Save detailed report
    with open(args.report, "w") as f:
        json.dump(
            {
                "total_time_seconds": total_time,
                "throughput_per_sec": len(results) / total_time,
                "results": [
                    {
                        "topic": r.topic,
                        "presentation_id": r.presentation_id,
                        "status": r.status,
                        "duration_seconds": r.duration,
                        "error": r.error,
                    }
                    for r in results
                ],
                "summary": {
                    "status_counts": dict(status_counts),
                    "success_rate": status_counts["success"] / len(results),
                },
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nFull report saved → {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
