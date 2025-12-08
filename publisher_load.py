import requests
import json
import time
import sys
import queue
import threading

API_BASE = "https://eew430la4k.execute-api.us-west-1.amazonaws.com/Prod"
TOPIC = "sports"

WORKERS = 10


def worker(q: "queue.Queue[tuple[int, dict]]"):
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            break

        i, payload = item
        t0 = time.time()
        try:
            r = requests.post(
                f"{API_BASE}/publish",
                headers={"Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=30,
            )
            r.raise_for_status()
            t1 = time.time()
            print(f"Published {i+1}, http_latency={(t1 - t0)*1000:.2f} ms")
        except Exception as e:
            print(f"[ERROR] publish {i+1}: {e}")
        finally:
            q.task_done()


def run_load(rate_msgs_per_sec: float, duration_sec: int):
    """
    rate_msgs_per_sec: 10 / 25 / 40
    duration_sec:      60
    """
    num_msgs = int(rate_msgs_per_sec * duration_sec)
    interval = 1.0 / rate_msgs_per_sec

    print(
        f"Sending {num_msgs} messages to topic='{TOPIC}' "
        f"at target {rate_msgs_per_sec} msg/s for {duration_sec} seconds"
    )

    q: "queue.Queue[tuple[int, dict]]" = queue.Queue()

    # Start worker threads
    workers = []
    for _ in range(WORKERS):
        t = threading.Thread(target=worker, args=(q,), daemon=True)
        t.start()
        workers.append(t)

    start = time.time()

    # Main loop: build payloads and enqueue them at the intended pace
    for i in range(num_msgs):
        sent_at = time.time()
        payload = {
            "topic": TOPIC,
            "sentAt": sent_at,
            "data": {
                "temp": 20 + (i % 20),
                "msg": f"msg-{i}",
            },
        }

        q.put((i, payload))

        # Maintain the desired publish cadence (10 / 25 / 40 msg/s)
        next_time = start + (i + 1) / rate_msgs_per_sec
        sleep_time = next_time - time.time()
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Wait for all publish tasks to finish
    q.join()

    # Stop worker threads cleanly
    for _ in workers:
        q.put(None)
    for t in workers:
        t.join()

    print("Done sending all messages.")


if __name__ == "__main__":
    rate = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0   # 10 / 25 / 40
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 60   # 60 seconds
    run_load(rate, duration)
