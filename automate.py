"""
AWS Lambda - EC2 Instance Manager

Purpose:
    Start, stop, or inspect EC2 instances from AWS Lambda.

Typical architecture:

    EventBridge Scheduler
            |
            v
       AWS Lambda
            |
            v
        Amazon EC2

Example environment variables:

    AWS_REGION=ap-south-1

    EC2_INSTANCE_IDS=
        i-0123456789abcdef0,
        i-0123456789abcdef1

    DEFAULT_ACTION=start

    DRY_RUN=false

Optional tag configuration:

    EC2_TAG_KEY=AutoStartStop
    EC2_TAG_VALUE=true

Example EventBridge payload:

    {
        "action": "start"
    }

or:

    {
        "action": "stop"
    }

or:

    {
        "action": "status"
    }
"""

import os
import json
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError


# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# -------------------------------------------------------------------
# Environment configuration
# -------------------------------------------------------------------

AWS_REGION = os.environ.get(
    "AWS_REGION",
    "ap-south-1"
)

DEFAULT_ACTION = os.environ.get(
    "DEFAULT_ACTION",
    "status"
).lower()

DRY_RUN = os.environ.get(
    "DRY_RUN",
    "false"
).lower() == "true"

INSTANCE_ID_STRING = os.environ.get(
    "EC2_INSTANCE_IDS",
    ""
)

TAG_KEY = os.environ.get(
    "EC2_TAG_KEY",
    ""
)

TAG_VALUE = os.environ.get(
    "EC2_TAG_VALUE",
    ""
)


# -------------------------------------------------------------------
# AWS client
# -------------------------------------------------------------------

ec2 = boto3.client(
    "ec2",
    region_name=AWS_REGION
)


# -------------------------------------------------------------------
# Utility functions
# -------------------------------------------------------------------

def utc_now():
    """Return the current UTC time as an ISO formatted string."""
    return datetime.now(timezone.utc).isoformat()


def parse_instance_ids():
    """
    Convert the EC2_INSTANCE_IDS environment variable into a list.

    Example:

        i-123,i-456,i-789

    becomes:

        ["i-123", "i-456", "i-789"]
    """

    if not INSTANCE_ID_STRING:
        return []

    instance_ids = []

    for item in INSTANCE_ID_STRING.split(","):
        instance_id = item.strip()

        if instance_id:
            instance_ids.append(instance_id)

    return instance_ids


def validate_action(action):
    """Validate the requested EC2 action."""

    valid_actions = {
        "start",
        "stop",
        "status"
    }

    if action not in valid_actions:
        raise ValueError(
            f"Invalid action '{action}'. "
            f"Allowed actions: {sorted(valid_actions)}"
        )


def get_instances_by_tags():
    """
    Find EC2 instances using an optional tag.

    Example:

        EC2_TAG_KEY=AutoStartStop
        EC2_TAG_VALUE=true
    """

    if not TAG_KEY or not TAG_VALUE:
        return []

    logger.info(
        "Searching instances with tag %s=%s",
        TAG_KEY,
        TAG_VALUE
    )

    response = ec2.describe_instances(
        Filters=[
            {
                "Name": f"tag:{TAG_KEY}",
                "Values": [TAG_VALUE]
            }
        ]
    )

    instance_ids = []

    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instance_id = instance.get("InstanceId")

            if instance_id:
                instance_ids.append(instance_id)

    return instance_ids


def get_target_instance_ids():
    """
    Get the instances that Lambda should manage.

    Explicit EC2_INSTANCE_IDS are preferred.

    If no IDs are supplied, tag-based discovery is used.
    """

    instance_ids = parse_instance_ids()

    if instance_ids:
        return instance_ids

    return get_instances_by_tags()


# -------------------------------------------------------------------
# EC2 information
# -------------------------------------------------------------------

def describe_instances(instance_ids):
    """
    Retrieve information about EC2 instances.
    """

    if not instance_ids:
        return []

    logger.info(
        "Describing instances: %s",
        instance_ids
    )

    response = ec2.describe_instances(
        InstanceIds=instance_ids
    )

    instances = []

    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):

            instances.append({
                "instance_id": instance.get(
                    "InstanceId"
                ),
                "state": instance.get(
                    "State", {}
                ).get(
                    "Name"
                ),
                "instance_type": instance.get(
                    "InstanceType"
                ),
                "private_ip": instance.get(
                    "PrivateIpAddress"
                ),
                "public_ip": instance.get(
                    "PublicIpAddress"
                ),
                "subnet_id": instance.get(
                    "SubnetId"
                ),
                "vpc_id": instance.get(
                    "VpcId"
                ),
                "availability_zone": instance.get(
                    "Placement", {}
                ).get(
                    "AvailabilityZone"
                ),
                "name": get_name_tag(instance)
            })

    return instances


def get_name_tag(instance):
    """
    Return the Name tag from an EC2 instance.
    """

    tags = instance.get("Tags", [])

    for tag in tags:

        if tag.get("Key") == "Name":
            return tag.get("Value")

    return None


# -------------------------------------------------------------------
# Status
# -------------------------------------------------------------------

def get_status(instance_ids):
    """
    Return the current status of all target instances.
    """

    instances = describe_instances(
        instance_ids
    )

    for instance in instances:

        logger.info(
            "Instance %s is %s",
            instance["instance_id"],
            instance["state"]
        )

    return instances


# -------------------------------------------------------------------
# Start EC2 instances
# -------------------------------------------------------------------

def start_instances(instance_ids):
    """
    Start stopped EC2 instances.

    Running instances are ignored.
    """

    instances = describe_instances(
        instance_ids
    )

    start_ids = []

    for instance in instances:

        state = instance["state"]
        instance_id = instance["instance_id"]

        if state == "stopped":
            start_ids.append(instance_id)

        elif state == "running":
            logger.info(
                "%s is already running",
                instance_id
            )

        else:
            logger.info(
                "%s is currently in state %s",
                instance_id,
                state
            )

    if not start_ids:
        return {
            "action": "start",
            "started": [],
            "message": "No stopped instances to start."
        }

    if DRY_RUN:
        logger.info(
            "DRY_RUN enabled. Would start: %s",
            start_ids
        )

        return {
            "action": "start",
            "dry_run": True,
            "would_start": start_ids
        }

    response = ec2.start_instances(
        InstanceIds=start_ids
    )

    logger.info(
        "Start request submitted: %s",
        start_ids
    )

    return {
        "action": "start",
        "started": start_ids,
        "response": response.get(
            "StartingInstances",
            []
        )
    }


# -------------------------------------------------------------------
# Stop EC2 instances
# -------------------------------------------------------------------

def stop_instances(instance_ids):
    """
    Stop running EC2 instances.

    Stopped instances are ignored.
    """

    instances = describe_instances(
        instance_ids
    )

    stop_ids = []

    for instance in instances:

        state = instance["state"]
        instance_id = instance["instance_id"]

        if state == "running":
            stop_ids.append(instance_id)

        elif state == "stopped":
            logger.info(
                "%s is already stopped",
                instance_id
            )

        else:
            logger.info(
                "%s is currently in state %s",
                instance_id,
                state
            )

    if not stop_ids:
        return {
            "action": "stop",
            "stopped": [],
            "message": "No running instances to stop."
        }

    if DRY_RUN:
        logger.info(
            "DRY_RUN enabled. Would stop: %s",
            stop_ids
        )

        return {
            "action": "stop",
            "dry_run": True,
            "would_stop": stop_ids
        }

    response = ec2.stop_instances(
        InstanceIds=stop_ids
    )

    logger.info(
        "Stop request submitted: %s",
        stop_ids
    )

    return {
        "action": "stop",
        "stopped": stop_ids,
        "response": response.get(
            "StoppingInstances",
            []
        )
    }


# -------------------------------------------------------------------
# Lambda handler
# -------------------------------------------------------------------

def lambda_handler(event, context):
    """
    Main AWS Lambda entry point.

    Event examples:

        {"action": "start"}

        {"action": "stop"}

        {"action": "status"}
    """

    logger.info(
        "Lambda execution started at %s",
        utc_now()
    )

    logger.info(
        "AWS region: %s",
        AWS_REGION
    )

    logger.info(
        "Incoming event: %s",
        json.dumps(event)
    )

    # ---------------------------------------------------------------
    # Determine action
    # ---------------------------------------------------------------

    action = DEFAULT_ACTION

    if isinstance(event, dict):

        event_action = event.get("action")

        if event_action:
            action = str(
                event_action
            ).lower()

    validate_action(action)

    # ---------------------------------------------------------------
    # Determine target instances
    # ---------------------------------------------------------------

    instance_ids = get_target_instance_ids()

    if not instance_ids:

        logger.warning(
            "No EC2 instances were found."
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "No EC2 instances found.",
                "action": action
            })
        }

    logger.info(
        "Target instances: %s",
        instance_ids
    )

    # ---------------------------------------------------------------
    # Execute action
    # ---------------------------------------------------------------

    if action == "start":

        result = start_instances(
            instance_ids
        )

    elif action == "stop":

        result = stop_instances(
            instance_ids
        )

    else:

        result = {
            "action": "status",
            "instances": get_status(
                instance_ids
            )
        }

    # ---------------------------------------------------------------
    # Return Lambda response
    # ---------------------------------------------------------------

    result["region"] = AWS_REGION
    result["timestamp"] = utc_now()

    logger.info(
        "Lambda execution completed: %s",
        json.dumps(result, default=str)
    )

    return {
        "statusCode": 200,
        "body": json.dumps(
            result,
            default=str
        )
    }


# -------------------------------------------------------------------
# Local testing
# -------------------------------------------------------------------

if __name__ == "__main__":

    print(
        "This file is designed to run in AWS Lambda."
    )

    print(
        "Configured region:",
        AWS_REGION
    )

    print(
        "Configured instances:",
        parse_instance_ids()
    )

    print(
        "Default action:",
        DEFAULT_ACTION
    )

    print(
        "Dry run:",
        DRY_RUN
    )