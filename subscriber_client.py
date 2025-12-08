import boto3
import json
import sys
import time
import csv
import os

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


def write_latency(latency_ms):
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["subscriberId", "latency_ms"])
        writer.writerow([SUBSCRIBER_ID, latency_ms])


print(f"Listening on queue: {QUEUE_URL} as subscriberId={SUBSCRIBER_ID}")

while True:
    resp = sqs.receive_message(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=2,
    )
    messages = resp.get("Messages", [])
    if not messages:
        continue

    for msg in messages:
        body = json.loads(msg["Body"])
        sent_at = body.get("sentAt")
        recv_at = time.time()
        latency_ms = None
        if sent_at is not None:
            # Raw latency (ms) based on server-side sentAt; keep signed to spot clock skew
            latency_ms = (recv_at - sent_at) * 1000.0

        print("=== Received message ===")
        print(json.dumps(body, indent=2))
        if latency_ms is not None:
            print("Latency (ms):", latency_ms)
        else:
            print("Latency (ms):", latency_ms)

        if latency_ms is not None:
            write_latency(latency_ms)

        sqs.delete_message(
            QueueUrl=QUEUE_URL,
            ReceiptHandle=msg["ReceiptHandle"],
        )
    time.sleep(0.1)
