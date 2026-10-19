import argparse
import json

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=180) as client:
        response = client.get("/api/studio/settings")
        response.raise_for_status()
        settings = response.json()
        response = client.post("/api/studio/settings/test", json={**settings["config"], "revision": settings["revision"]})
        if not response.is_success:
            raise RuntimeError(response.json().get("detail", "Model connection failed"))
        print(json.dumps(response.json(), ensure_ascii=False))


if __name__ == "__main__":
    main()
