import argparse
import asyncio
import time

from dotenv import load_dotenv
from openai import AsyncOpenAI

from studio.agents import AgentSettings


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--thinking", choices=["auto", "disabled", "enabled"], default="disabled")
    args = parser.parse_args()
    load_dotenv()
    settings = AgentSettings.from_environment()
    async with AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url,
                           timeout=40, max_retries=0) as client:
        start = time.perf_counter()
        response = await client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "system", "content": "Return concise JSON only."},
                      {"role": "user", "content": "Plan a park drawing with a house, two trees, sun and clouds. Return three independent task goals as JSON."}],
            extra_body={"thinking": {"type": args.thinking}},
        )
        print({"model": response.model, "latency_ms": round((time.perf_counter() - start) * 1000),
               "content_length": len(response.choices[0].message.content or "")})


if __name__ == "__main__":
    asyncio.run(main())
