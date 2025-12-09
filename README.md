# AWS Serverless Pub/Sub System

Serverless publish/subscribe broker built on AWS:

- **API Gateway** exposes `/subscribe` and `/publish`
- **Lambda** hosts the broker logic with in-memory + Redis caching
- **DynamoDB** stores (topic, subscriberId, filters, queueUrl, function)
- **SQS FIFO** queue per subscriber to guarantee ordering
- **Local Python tools** generate subscribers, send load, and measure latency

It supports topic, content, and function-based matching and was designed to reproduce the experiments from the “Serverless Publish/Subscribe System” paper with AWS primitives.

---

## 1. Architecture Overview

```
Publisher --> API Gateway --> Lambda (/publish) --> DynamoDB + Redis caches
                                                --> ThreadPoolExecutor --> SQS (per subscriber)
Subscriber <-- SQS FIFO queue <-- Lambda (/subscribe) <-- API Gateway <-- subscriber client
```

- Registrations (`/subscribe`) create a unique FIFO queue and insert one item per (topic, subscriberId) into DynamoDB.
- Publications (`/publish`) fan out in parallel (up to 20 threads) after evaluating content/function filters.
- Local scripts (`publisher_load.py`, `subscriber_client.py`) send traffic with `sentAt` timestamps and log delivery latency.

---

## 2. Setup

### 2.1 Prerequisites

- AWS account with permission to deploy Lambda, API Gateway, DynamoDB, SQS, CloudFormation.
- Locally installed:
  - Python 3.9+
  - AWS CLI (`aws configure`)
  - AWS SAM CLI
- S3 bucket for SAM artifacts (created automatically when running `sam deploy --guided`).

### 2.2 Deploy with SAM

```bash
cd aws-pubsub
sam build
sam deploy --guided
```

SAM prompts for stack name, region, and artifact bucket. After deployment it prints:

```
ApiUrl = https://<id>.execute-api.<region>.amazonaws.com/Prod
```

Use this URL as `API_BASE` in all scripts.

### 2.3 Local Python Environment

```bash
cd ~/aws/aws-pubsub
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # installs requests, pandas, etc.
```

Activate the venv every time you work on the project:

```bash
source .venv/bin/activate
```

---

## 3. API Reference

### 3.1 `POST /subscribe`

Registers a subscriber, creates its FIFO SQS queue, and stores metadata in DynamoDB.

**Request body**

```json
{
  "topics": ["sports"],
  "filters": { "temp": ">30" },
  "function": "lambda pub: \"goal\" in pub.get(\"msg\", \"\").lower()"
}
```

* `topics` – required list of topics.
* `filters` – optional simple numeric/string comparisons.
* `function` – optional lambda expression evaluated server-side.

**Response**

```json
{
  "subscriberId": "b54c9ff2-…",
  "queueUrl": "https://sqs.../subscriber-b54c9ff2-….fifo",
  "topics": ["sports"],
  "filters": {},
  "function": ""
}
```

Examples:

- Topic-only:

  ```bash
  curl -X POST "$API_BASE/subscribe" \
    -H "Content-Type: application/json" \
    -d '{"topics":["sports"],"filters":{}}'
  ```

- Content filter (`temp > 30`):

  ```bash
  curl -X POST "$API_BASE/subscribe" \
    -H "Content-Type: application/json" \
    -d '{"topics":["sports"],"filters":{"temp":">30"}}'
  ```

- Function filter:

  ```bash
  curl -X POST "$API_BASE/subscribe" \
    -H "Content-Type: application/json" \
    -d '{"topics":["sports"],"function":"lambda pub: \"goal\" in pub.get(\"msg\",\" \").lower()"}'
  ```

### 3.2 `POST /publish`

Publishes data to a topic. Include `sentAt` to measure end-to-end latency.

```bash
curl -X POST "$API_BASE/publish" \
  -H "Content-Type: application/json" \
  -d '{
        "topic": "sports",
        "sentAt": 1730000000.123,
        "data": { "temp": 35, "msg": "Last minute GOAL" }
      }'
```

If `sentAt` is omitted the Lambda sets it to `time.time()` before forwarding.

---

## 4. Local Tooling

### 4.1 `subscriber_client.py`

Reads a subscriber’s SQS queue, prints each message, and writes latency to `latencies_<SUBSCRIBER_ID>.csv`.

```bash
python subscriber_client.py <QUEUE_URL> <SUBSCRIBER_ID>
```

Keep the client running before you start load tests to avoid queue backlogs that inflate latency numbers.

### 4.2 `publisher_load.py`

Sends messages at a fixed rate and duration using a thread pool so the target rate is respected even when HTTP requests are slow.

```bash
python publisher_load.py <RATE_MSG_PER_SEC> <DURATION_SECONDS>
# e.g.
python publisher_load.py 10 60
python publisher_load.py 25 60
python publisher_load.py 40 60
```

Each payload is:

```json
{ "topic": "sports", "sentAt": <now>, "data": { "temp": 20 + (i % 20), "msg": "msg-i" } }
```

### 4.3 `create_subs.py`

Bulk-create subscribers (topic/content/function filters can be adjusted inside the script or via `payload_override`).

```bash
python create_subs.py 50
```

Besides printing each `subscriberId`, the script writes every `(subscriberId, queueUrl)` pair to `subscribers.txt` so you can launch the matching subscriber clients in bulk.

Override examples (run from repo root):

```bash
# Multiple topics, no filter
python - <<'PY'
from create_subs import create_subscribers
create_subscribers(10, {"topics": ["sports", "weather"], "filters": {}})
PY

# Content filter: temp > 30
python - <<'PY'
from create_subs import create_subscribers
create_subscribers(10, {"topics": ["sports"], "filters": {"temp": ">30"}})
PY

# Function filter
python - <<'PY'
from create_subs import create_subscribers
create_subscribers(10, {
  "topics": ["sports"],
  "filters": {},
  "function": "lambda pub: 'goal' in pub.get('msg','').lower()",
})
PY
```

### 4.4 `analyze_results.py`

Aggregates every `latencies_*.csv` in the current directory and prints mean/p95/p99 statistics. Copy the CSVs for a specific scenario into the working directory before running it.

```bash
python analyze_results.py
```

### 4.5 Launching all subscriber clients + cleaning latency files

Once `subscribers.txt` exists (generated by `create_subs.py`), spin up a client per queue:

```bash
mkdir -p logs
while read sub queue; do
  python subscriber_client.py "$queue" "$sub" > logs/$sub.log &
done < subscribers.txt
```

Each process keeps its stdout in `logs/<SUB_ID>.log` and writes latency to `latencies_<SUB_ID>.csv`.
Stop them when needed via `pkill -f subscriber_client.py`.

Before starting a new scenario, delete old latency CSVs in the repo root:

```bash
find . -maxdepth 1 -name 'latencies_*.csv' -delete
```

---

## 5. Experiment Workflow

1. **Create subscribers** – use `create_subs.py` or curl to register the desired number of subscribers with the correct filters.
2. **Start subscriber clients** – run `subscriber_client.py` for each queue so latency is captured while load runs.
3. **Run load** – execute `publisher_load.py <rate> <duration>` (e.g., 10/25/40 msg/s) to generate traffic with `sentAt`.
4. **Organize data** – move the generated CSVs into scenario-specific folders:

   ```bash
   mkdir -p results/50subs_10mps
   mv latencies_*.csv results/50subs_10mps/
   ```

5. **Analyze** – copy the scenario’s CSVs back or run `analyze_results.py` directly inside the folder to compute averages for comparison with the paper’s charts.
6. **Manual sanity checks** – use `curl` to publish sample messages and verify topic/content/function behavior before large runs.

This workflow mirrors the paper’s methodology: keep publishers and subscribers on the same clock, send `sentAt`, and compute the arithmetic mean of all message latencies per scenario.

---

## 6. Maintenance & Tuning

- **Clean up SQS queues:** every subscriber has its own FIFO queue named `subscriber-<UUID>.fifo`. Delete them when resetting the environment:

  ```bash
  aws sqs list-queues --queue-name-prefix subscriber-

  for url in $(aws sqs list-queues --queue-name-prefix subscriber- --query 'QueueUrls[]' --output text); do
    echo "Deleting $url"
    aws sqs delete-queue --queue-url "$url"
  done
  ```

- **Caching knobs:** `publish_message.py` uses an in-Lambda cache (`CACHE_TTL = 10s`) and Redis cache (`REDIS_TTL = 30s`) to limit DynamoDB reads. Set them to `0` to disable caching if you are experimenting with different consistency/performance trade-offs.
- **Fan-out parallelism:** `MAX_WORKERS = 20` threads send SQS messages concurrently. Increase/decrease this constant to study how parallelism affects publish latency and Lambda duration.

---

## 7. Common Commands Cheat Sheet

```bash
# Activate virtualenv
cd ~/aws/aws-pubsub
source .venv/bin/activate

# Deploy stack
sam build && sam deploy

# Create N subscribers
python create_subs.py 25

# Start all subscriber clients
mkdir -p logs
while read sub queue; do
  python subscriber_client.py "$queue" "$sub" > logs/$sub.log &
done < subscribers.txt

# Run load: 25 msg/s for 60 seconds
python publisher_load.py 25 60

# Analyze latency CSVs in current directory
python analyze_results.py

# Manual publish via curl
API_BASE="https://eew430la4k.execute-api.us-west-1.amazonaws.com/Prod"
curl -X POST "$API_BASE/publish" \
  -H "Content-Type: application/json" \
  -d '{"topic":"sports","data":{"temp":35,"msg":"Last minute GOAL"}}'
```

With these steps you can deploy the system, create topic/content/function subscribers, run client scripts, reproduce the load experiments from the paper, and analyze the results on AWS infrastructure.
sudo yum install -y python3.11
find . -maxdepth 1 -name 'latencies_*.csv' -delete

mkdir -p logs
while read sub queue; do
  python subscriber_client.py "$queue" "$sub" > logs/$sub.log &
done < subs_vm4.txt
