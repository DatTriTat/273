import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from boto3.dynamodb.conditions import Key
import redis  # NEW
from botocore.config import Config

from utils import matches_filter

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])

sqs = boto3.client("sqs", config=Config(max_pool_connections=20))

# ----- Redis client (ElastiCache) -----
# Because your cluster has "Encryption in transit: Required",
# we enable SSL. For demo we set ssl_cert_reqs=None to avoid
# certificate hassles; in production you should validate certs.
redis_client = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ.get("REDIS_PORT", 6379)),
    ssl=True,
    ssl_cert_reqs=None,
    decode_responses=True,
)

# Keep Redis entries short-lived so topic changes propagate quickly.
REDIS_TTL = 30  # seconds for Redis cache

# In-Lambda cache:
# topic -> { "ts": float, "subs": [...], "compiled": {subscriberId: fn or None} }
SUB_CACHE = {}
CACHE_TTL = 10  # seconds

# Maximum number of threads for parallel SQS sends
MAX_WORKERS = 20


def get_subscribers_for_topic(topic: str):
    """
    Fetch subscribers for the topic using:
      1) In-Lambda cache (SUB_CACHE)
      2) Redis cache (shared across Lambda instances)
      3) DynamoDB query on cache miss
    Also precompile function filters for each subscriber.
    """
    now = time.time()

    # 1) In-Lambda cache
    cached = SUB_CACHE.get(topic)
    if cached and (now - cached["ts"] < CACHE_TTL):
        return cached["subs"], cached["compiled"]

    subs = None

    # 2) Redis cache
    redis_key = f"subs:{topic}"
    try:
        cached_json = redis_client.get(redis_key)
        if cached_json:
            subs = json.loads(cached_json)
            print(f"[CACHE] HIT Redis for topic={topic}, {len(subs)} subs")
    except Exception as e:
        print(f"[CACHE] Redis GET failed for {redis_key}: {e}")
        subs = None

    # 3) DynamoDB on Redis miss
    if subs is None:
        print(f"[CACHE] MISS Redis for topic={topic}, querying DynamoDB")
        resp = table.query(
            KeyConditionExpression=Key("topic").eq(topic)
        )
        subs = resp.get("Items", [])

        # Write back to Redis (best-effort)
        try:
            redis_client.setex(redis_key, REDIS_TTL, json.dumps(subs))
            print(f"[CACHE] Stored {len(subs)} subs in Redis for topic={topic}")
        except Exception as e:
            print(f"[CACHE] Redis SETEX failed for {redis_key}: {e}")

    # Precompile functions so each message publish does not eval user code repeatedly.
    compiled = {}
    for sub in subs:
        fn_code = sub.get("function") or ""
        if fn_code:
            try:
                fn = eval(fn_code, {"__builtins__": {}}, {})
            except Exception as e:
                print(f"[FUNC] Compile error for sub={sub.get('subscriberId')}: {e}")
                fn = None
        else:
            fn = None
        compiled[sub["subscriberId"]] = fn

    # Update in-Lambda cache
    SUB_CACHE[topic] = {"ts": now, "subs": subs, "compiled": compiled}
    return subs, compiled


def send_to_subscriber(sub, compiled_fn, topic, data, sent_at) -> bool:
    import threading, time as _time

    thread_id = threading.get_ident()
    thread_name = threading.current_thread().name
    start = _time.time()

    print(f"[Thread {thread_name} / {thread_id}] START sub={sub['subscriberId']}")

    queue_url = sub.get("queueUrl")
    if not queue_url:
        return False

    filters = sub.get("filters", {}) or {}

    # Content-based filter
    if not matches_filter(data, filters):
        print(f"[Thread {thread_name}] FILTER(content) FAIL sub={sub['subscriberId']}")
        return False

    # Function-based filter (precompiled)
    if compiled_fn is not None:
        try:
            if not bool(compiled_fn(data)):
                print(f"[Thread {thread_name}] FILTER(func) FAIL sub={sub['subscriberId']}")
                return False
        except Exception as e:
            print(f"[Thread {thread_name}] FILTER(func) ERROR sub={sub['subscriberId']}: {e}")
            return False

    message_body = {
        "subscriberId": sub["subscriberId"],
        "topic": topic,
        "data": data,
        "sentAt": sent_at,
    }

    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message_body),
        MessageGroupId=sub["subscriberId"],
    )

    end = _time.time()
    print(
        f"[Thread {thread_name} / {thread_id}] "
        f"DONE sub={sub['subscriberId']} in {(end - start) * 1000:.2f} ms"
    )
    return True


def lambda_handler(event, context):
    """
    POST /publish
    Body JSON:
    {
      "topic": "sports",
      "sentAt": 1730000000.123,   # optional; server sets the timestamp if missing
      "data": {"temp": 35, "msg": "goal!"}
    }
    """
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON"})}

    topic = body.get("topic")
    data = body.get("data")

    if not topic or data is None:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "topic and data are required"}),
        }

    # Respect client clock if publisher_load.py sends sentAt
    sent_at = body.get("sentAt")
    if sent_at is None:
        sent_at = time.time()

    # Fetch subscribers + compiled functions (with Redis + in-Lambda cache)
    subscribers, compiled = get_subscribers_for_topic(topic)

    if not subscribers:
        return {
            "statusCode": 200,
            "body": json.dumps({"delivered": 0}),
        }

    delivered = 0

    # Fan out in parallel across subscribers
    worker_count = min(MAX_WORKERS, len(subscribers))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = []
        for sub in subscribers:
            sub_id = sub["subscriberId"]
            fn = compiled.get(sub_id)
            futures.append(
                executor.submit(send_to_subscriber, sub, fn, topic, data, sent_at)
            )

        for f in as_completed(futures):
            try:
                if f.result():
                    delivered += 1
            except Exception as e:
                # Ignore per-subscriber failure to keep request successful
                print(f"[ERROR] send_to_subscriber raised: {e}")

    return {
        "statusCode": 200,
        "body": json.dumps({"delivered": delivered}),
    }
