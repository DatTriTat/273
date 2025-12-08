import boto3
import json
import sys
import time
import csv
import os

# Try to offset local clock skew using NTP if available.
try:
    import ntplib
except ImportError:
    ntplib = None

"""
Usage:
  python subscriber_client.py <QUEUE_URL> <SUBSCRIBER_ID>
"""

if len(sys.argv) != 3:
    print("Usage: python subscriber_client.py <QUEUE_URL> <SUBSCRIBER_ID>")
    sys.exit(1)

QUEUE_URL = sys.argv[1]
SUBSCRIBER_ID = sys.argv[2]

sqs = boto3.client("sqs")

CSV_FILE = f"latencies_{SUBSCRIBER_ID}.csv"


def get_time_offset() -> float:
    """
    Best-effort NTP offset (seconds) so recv_at aligns closer to server time.
    If ntplib is missing or NTP fails, return 0.0.
    """
    if ntplib is None:
        return 0.0
    try:
        client = ntplib.NTPClient()
        resp = client.request("pool.ntp.org", version=3, timeout=2)
        return resp.offset
    except Exception as e:
        print(f"[WARN] NTP sync failed, using offset 0. Error: {e}")
        return 0.0


TIME_OFFSET = get_time_offset()
print(f"NTP time offset (s): {TIME_OFFSET:.6f}")


def write_latency(cloud_ms, e2e_ms):
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["subscriberId", "cloud_latency_ms", "end_to_end_ms"])
        writer.writerow([SUBSCRIBER_ID, cloud_ms, e2e_ms])


print(f"Listening on queue: {QUEUE_URL} as subscriberId={SUBSCRIBER_ID}")

while True:
    resp = sqs.receive_message(
        QueueUrl=QUEUE_URL,
        AttributeNames=["SentTimestamp"],
        MaxNumberOfMessages=10,
        WaitTimeSeconds=2,
    )
    messages = resp.get("Messages", [])
    if not messages:
        continue

    for msg in messages:
        body = json.loads(msg["Body"])
        sent_at = body.get("sentAt")  # Lambda/client timestamp

        sqs_ts = msg.get("Attributes", {}).get("SentTimestamp")
        sqs_arrival = int(sqs_ts) / 1000.0 if sqs_ts else None

        recv_at = time.time() + TIME_OFFSET

        cloud_ms = None
        if sent_at is not None and sqs_arrival is not None:
            cloud_ms = (sqs_arrival - sent_at) * 1000.0

        e2e_ms = None
        if sent_at is not None:
            e2e_ms = (recv_at - sent_at) * 1000.0

        print("=== Received message ===")
        print(json.dumps(body, indent=2))
        print(f"SentTimestamp (SQS): {sqs_arrival}")
        print(f"Latency cloud (ms): {cloud_ms}")
        print(f"Latency end-to-end (ms): {e2e_ms}")

        if cloud_ms is not None or e2e_ms is not None:
            write_latency(cloud_ms, e2e_ms)

        sqs.delete_message(
            QueueUrl=QUEUE_URL,
            ReceiptHandle=msg["ReceiptHandle"],
        )
    time.sleep(0.1)
