import json
import sys
import requests

API_BASE = "https://eew430la4k.execute-api.us-west-1.amazonaws.com/Prod"


def create_subscribers(n: int, payload_override: dict | None = None):
    subs = []
    for i in range(n):
        payload = payload_override or {
            "topics": ["sports"],
            "filters": {},
            # "function": "lambda pub: pub.get('temp',0) > 30"
        }
        r = requests.post(
            f"{API_BASE}/subscribe",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
        )
        try:
            r.raise_for_status()
        except Exception as e:
            print(f"[{i+1}] Error:", e, "response:", r.text)
            continue

        resp = r.json()
        subs.append(resp)
        print(f"[{i+1}] subscriberId={resp['subscriberId']}")
    return subs


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    subscribers = create_subscribers(n)
    with open("subscribers.txt", "w") as f:
        for sub in subscribers:
            f.write(f"{sub['subscriberId']} {sub['queueUrl']}\n")
    print(f"Wrote {len(subscribers)} entries to subscribers.txt")
