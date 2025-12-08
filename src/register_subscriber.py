import json
import os
import uuid

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])

sqs = boto3.client("sqs")


def lambda_handler(event, context):
    """
    POST /subscribe
    Body JSON:
    {
      "topics": ["sports", "weather"],
      "filters": {"temp": ">30"},
      "function": "lambda pub: 'goal' in pub.get('msg','').lower()"
    }
    """
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON"})}

    topics = body.get("topics", [])
    filters = body.get("filters", {})
    fn_code = body.get("function", "")

    if not isinstance(topics, list) or not topics:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "topics must be a non-empty list"}),
        }

    sub_id = str(uuid.uuid4())
    # Each subscriber gets a FIFO queue so messages stay ordered per subscriber.
    queue_name = f"subscriber-{sub_id}.fifo"

    # create one SQS queue per subscriber
    sqs_resp = sqs.create_queue(
        QueueName=queue_name,
        Attributes={
            "FifoQueue": "true",
            "ContentBasedDeduplication": "true",
        },
    )
    queue_url = sqs_resp["QueueUrl"]

    # store one item per (topic, subscriberId)
    for t in topics:
        item = {
            "topic": t,
            "subscriberId": sub_id,
            "filters": filters,
            "queueUrl": queue_url,
            "function": fn_code,
        }
        table.put_item(Item=item)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "subscriberId": sub_id,
                "queueUrl": queue_url,
                "topics": topics,
                "filters": filters,
                "function": fn_code,
            }
        ),
    }
