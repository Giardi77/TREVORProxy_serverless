#!/usr/bin/python3
import argparse
import os
import signal
import sys
import threading
import time
import uuid

import boto3
from botocore.exceptions import ProfileNotFound
from threadlocal_aws.clients import ec2, ecs
from trevorproxy.cli import main as trevorproxy

from . import infra_manager

queue = None
message_id = None


def terminate(sig, frame):
    message = queue.receive_messages(MaxNumberOfMessages=1)[0]
    queue.delete_messages(Entries=[{"Id": message_id, "ReceiptHandle": message.receipt_handle}])
    exit(0)


def send_proxy_intent():
    global message_id
    print("Sending proxy intent.")
    dedup_id = str(uuid.uuid4())
    message_id = queue.send_message(MessageBody="{}", MessageDeduplicationId=dedup_id, MessageGroupId=dedup_id)["MessageId"]


def run_command(args, profile=None):  # Renamed to run_command and accepts args
    global queue

    if os.getuid() != 0:
        print("Error: The 'run' command requires root privileges.")
        print("Please run this command using 'sudo'. For example: sudo trevorproxy_serverless run ...")
        sys.exit(1)

    # If running with sudo, boto3 won't find the user's AWS profile.
    # We need to point it to the right place.
    if "SUDO_USER" in os.environ:
        home_dir = os.path.expanduser(f"~{os.environ['SUDO_USER']}")
        aws_config_path = os.path.join(home_dir, ".aws", "config")
        aws_credentials_path = os.path.join(home_dir, ".aws", "credentials")
        if os.path.exists(aws_config_path):
            os.environ["AWS_CONFIG_FILE"] = aws_config_path
        if os.path.exists(aws_credentials_path):
            os.environ["AWS_SHARED_CREDENTIALS_FILE"] = aws_credentials_path

    # Ensure all boto3 clients (including those from threadlocal_aws) use the correct profile.
    if profile:
        os.environ["AWS_PROFILE"] = profile

    try:
        session = boto3.Session(profile_name=profile)
        # Verify the identity to ensure we're using the correct profile
        identity = session.client("sts").get_caller_identity()
        print(f"INFO: Running as AWS principal: {identity['Arn']}")
    except ProfileNotFound:
        print(f"Error: The AWS profile '{profile}' could not be found.")
        if "SUDO_USER" in os.environ:
            print(f"Attempted to use credentials for user '{os.environ['SUDO_USER']}' but failed.")
            print("Please ensure that the AWS config and credentials files exist in the correct home directory (~/.aws/).")
        sys.exit(1)
    sqs = session.resource("sqs")
    queue = sqs.get_queue_by_name(QueueName="proxy-intents.fifo")
    send_proxy_intent()

    # setup graceful termination to remove proxy-intent message
    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGTERM, terminate)

    # sliding window, to ensure messages\'t expire while the tool is running
    interval = int(queue.attributes["MessageRetentionPeriod"]) / 2
    timer = threading.Timer(interval, send_proxy_intent)
    timer.start()

    ecs_client = ecs()
    cluster = ecs_client.describe_clusters(clusters=["proxy-cluster"])["clusters"][0]

    print("Waiting for proxies to spin up..")
    while True:
        taskArns = ecs_client.list_tasks(cluster=cluster["clusterArn"], family="proxy-def")["taskArns"]
        if taskArns:
            tasks = ecs_client.describe_tasks(cluster=cluster["clusterArn"], tasks=taskArns)
            if not [t for t in tasks["tasks"] if t["containers"][0]["lastStatus"] != "RUNNING"]:
                break
        time.sleep(10)

    ec2_client = ec2()
    taskENIIds = [t["attachments"][0]["details"][1]["value"] for t in tasks["tasks"]]
    taskENIs = ec2_client.describe_network_interfaces(NetworkInterfaceIds=taskENIIds)["NetworkInterfaces"]
    proxyIps = ["root@" + e["Association"]["PublicIp"] for e in taskENIs]

    # prepare sys.argv for the call into trevorproxy
    trevorArgs = [sys.argv[0], "-p", str(args.port), "-l", args.listen_address, "ssh", "-k", args.key, "--base-port", str(args.base_port)]
    for i in range(len(trevorArgs), len(trevorArgs) + len(proxyIps)):
        trevorArgs.append(proxyIps[i - 11])
    sys.argv = trevorArgs

    trevorproxy()


def main():
    parser = argparse.ArgumentParser(description="TREVORproxy Serverless CLI")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Infra command parser
    infra_parser = subparsers.add_parser("infra", help="Manage TREVORproxy serverless infrastructure")
    infra_parser.add_argument("action", choices=["up", "down"], help="Action to perform: 'up' to deploy, 'down' to destroy")
    infra_parser.add_argument("--profile", default="tps", help="Use a specific AWS profile from your credentials file (default: tps)")
    infra_parser.add_argument("--proxy-count", type=int, help="The number of SOCKS proxies to spin up.")

    # Run command parser
    run_parser = subparsers.add_parser("run", help="Run TREVORproxy with the serverless cluster")
    run_parser.add_argument("-k", "--key", default="~/.ssh/trevorproxy", help="Use this SSH key when connecting to proxy hosts", required=False)
    run_parser.add_argument("-p", "--port", type=int, default=1080, help="Port for SOCKS server to listen on (default: 1080)")
    run_parser.add_argument("-l", "--listen-address", default="127.0.0.1", help="Listen address for SOCKS server (default: 127.0.0.1)")
    run_parser.add_argument("--base-port", default=32482, type=int, help="Base listening port to use for SOCKS proxies (default: 32482)")
    run_parser.add_argument("--profile", default="tps", help="Use a specific AWS profile from your credentials file (default: tps)")

    args = parser.parse_args()

    if args.command == "infra":
        if args.action == "up":
            infra_manager.up(profile=args.profile, proxy_count=args.proxy_count)
        elif args.action == "down":
            infra_manager.down(profile=args.profile, proxy_count=args.proxy_count)
    elif args.command == "run":
        run_command(args, profile=args.profile)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
