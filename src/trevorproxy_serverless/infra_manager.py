import json
import os
import subprocess
import sys
from importlib import resources


def _run_terraform_command(command, public_key=None, profile=None, proxy_count=None):
    """
    Helper function to run terraform commands.
    """
    # When installed as a package, "infra" is package data.
    with resources.path("trevorproxy_serverless", "infra") as tf_path:
        tf_dir = str(tf_path)

    # Check if terraform is installed
    try:
        subprocess.run(["terraform", "--version"], check=True, capture_output=True)
    except FileNotFoundError:
        print("Error: terraform command not found. Please ensure Terraform is installed and in your PATH.")
        sys.exit(1)

    # Prepare environment for subprocess calls to AWS CLI
    env = os.environ.copy()
    if profile:
        env["AWS_PROFILE"] = profile
        print(f"Using AWS profile: {profile}")  # Debug print

    # Initialize Terraform
    print(f"Initializing Terraform in {tf_dir}...")
    subprocess.run(["terraform", f"-chdir={tf_dir}", "init"], check=True, env=env)

    # Prepare variables for terraform apply/destroy
    tf_vars = []
    if public_key is not None:
        tf_vars.extend(["-var", f"public_key={public_key}"])
    else:  # For destroy, public_key is empty string
        tf_vars.extend(["-var", "public_key="])

    if profile:
        tf_vars.extend(["-var", f"profile={profile}"])

    if proxy_count is not None:
        tf_vars.extend(["-var", f"proxy_count={proxy_count}"])

    full_command = ["terraform", f"-chdir={tf_dir}", command, "-auto-approve"] + tf_vars
    print(f"Running: {' '.join(full_command)}")
    subprocess.run(full_command, check=True, env=env)


def _profile_exists(profile_name):
    """Checks if an AWS CLI profile exists."""
    try:
        subprocess.run(["aws", "configure", "get", "aws_access_key_id", "--profile", profile_name], check=True, capture_output=True, text=True, env=os.environ.copy())
        return True
    except subprocess.CalledProcessError:
        return False


def _create_aws_profile(profile_name, access_key_id, secret_access_key, region):
    if not region:
        region = "us-east-2"
    """Creates a new AWS CLI profile."""
    print(f"Creating AWS CLI profile '{profile_name}'...")
    subprocess.run(["aws", "configure", "set", "aws_access_key_id", access_key_id, "--profile", profile_name], check=True)
    subprocess.run(["aws", "configure", "set", "aws_secret_access_key", secret_access_key, "--profile", profile_name], check=True)
    subprocess.run(["aws", "configure", "set", "region", region, "--profile", profile_name], check=True)
    subprocess.run(["aws", "configure", "set", "output", "json", "--profile", profile_name], check=True)
    print(f"AWS CLI profile '{profile_name}' created successfully.")


def _get_aws_account_id(profile_name=None):
    """Retrieves the AWS account ID for a given profile."""
    cmd = ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"]
    if profile_name:
        cmd.extend(["--profile", profile_name])
    try:
        env = os.environ.copy()
        if profile_name:  # Ensure AWS_PROFILE is set for this call
            env["AWS_PROFILE"] = profile_name
        account_id = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env).stdout.strip()
        return account_id
    except subprocess.CalledProcessError as e:
        print(f"Error getting AWS account ID: {e.stderr.strip()}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: aws command not found. Please ensure AWS CLI is installed and in your PATH.")
        sys.exit(1)


def _create_iam_policy_and_user(target_profile):
    """
    Guides the user through creating the necessary IAM policy and user,
    then configures the local AWS CLI profile.
    """
    print("\n--- Setting up AWS IAM for TREVORproxy Serverless ---")
    print("This requires temporary administrative permissions.")

    # Determine which profile to use for IAM operations
    admin_profile = input("Enter the AWS CLI profile to use for creating IAM resources (must have admin permissions, e.g., 'default'): ").strip() or "default"

    # Get account ID using the admin profile
    account_id = _get_aws_account_id(admin_profile)
    print(f"Using AWS Account ID: {account_id}")

    policy_name = "tps-least-privilege-policy"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
    user_name = target_profile  # "tps"

    # The policy file is packaged with the application. We use importlib.resources
    # to get a path to it that the AWS CLI can access.
    print(f"Creating/updating IAM policy '{policy_name}'...")
    try:
        with resources.path("trevorproxy_serverless.data", f"{policy_name}.json") as policy_path:
            policy_json_path = str(policy_path)
            subprocess.run(
                ["aws", "--profile", admin_profile, "iam", "create-policy", "--policy-name", policy_name, "--policy-document", f"file://{policy_json_path}"], check=True, capture_output=True
            )
        print(f"Policy '{policy_name}' created.")
    except subprocess.CalledProcessError as e:
        if "EntityAlreadyExists" in e.stderr.decode():
            print(f"Policy '{policy_name}' already exists.")
            # Optionally, update policy version here if desired, but for least privilege, simpler to assume consistent policy.
        else:
            print(f"Error creating policy: {e.stderr.decode()}")
            sys.exit(1)

    print(f"Creating/updating IAM user '{user_name}'...")
    try:
        subprocess.run(["aws", "--profile", admin_profile, "iam", "create-user", "--user-name", user_name], check=True, capture_output=True)
        print(f"User '{user_name}' created.")
    except subprocess.CalledProcessError as e:
        if "EntityAlreadyExists" in e.stderr.decode():
            print(f"User '{user_name}' already exists.")
        else:
            print(f"Error creating user: {e.stderr.decode()}")
            sys.exit(1)

    print(f"Creating access keys for user '{user_name}'...")
    try:
        result = subprocess.run(["aws", "--profile", admin_profile, "iam", "create-access-key", "--user-name", user_name], check=True, capture_output=True, text=True)
        access_key_data = json.loads(result.stdout)
        access_key_id = access_key_data["AccessKey"]["AccessKeyId"]
        secret_access_key = access_key_data["AccessKey"]["SecretAccessKey"]
        print("Access keys created. ATTENTION: Save your SecretAccessKey, it will not be shown again.")
    except subprocess.CalledProcessError as e:
        if "LimitExceeded" in e.stderr.decode():
            print(f"User '{user_name}' already has max access keys. Using existing. Please delete old keys manually if needed.")
            # For simplicity, we assume existing keys will be used if max reached. This might need more robust handling.
            # Alternatively, if we need to ensure *new* keys, list existing and delete before creating.
            # For now, if creation fails due to limit, we can't proceed without user intervention.
            sys.exit(1)  # Exit to force user to resolve key limit
        else:
            print(f"Error creating access keys: {e.stderr.decode()}")
            sys.exit(1)
    except Exception as e:
        print(f"Unexpected error creating access keys: {e}")
        sys.exit(1)

    print(f"Attaching policy '{policy_name}' to user '{user_name}'...")
    try:
        subprocess.run(["aws", "--profile", admin_profile, "iam", "attach-user-policy", "--user-name", user_name, "--policy-arn", policy_arn], check=True, capture_output=True)
        print(f"Policy '{policy_name}' attached to user '{user_name}'.")
    except subprocess.CalledProcessError as e:
        print(f"Error attaching policy: {e.stderr.decode()}")
        sys.exit(1)

    # Configure the target profile locally
    print(f"Configuring local AWS CLI profile '{target_profile}'...")
    region = input(f"Enter the default AWS region for profile '{target_profile}' (e.g., eu-west-2): ").strip()
    _create_aws_profile(target_profile, access_key_id, secret_access_key, region)

    print(f"\nAWS profile '{target_profile}' setup complete. You can now use this profile.")


def up(profile=None, proxy_count=None):
    """
    Deploys the TREVORproxy serverless infrastructure using Terraform.
    """
    if profile == "tps":
        if not _profile_exists(profile):
            print(f"AWS CLI profile '{profile}' not found.")
            _create_iam_policy_and_user(profile)

    print("Preparing SSH key...")
    ssh_dir = os.path.expanduser("~/.ssh")
    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

    ssh_key_path = os.path.join(ssh_dir, "trevorproxy")
    public_key_path = os.path.join(ssh_dir, "trevorproxy.pub")

    if not os.path.exists(ssh_key_path):
        print(f"Generating new SSH key pair: {ssh_key_path}")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", ssh_key_path, "-C", "trevorproxy", "-N", ""], check=True)
    else:
        print(f"Using existing SSH key: {ssh_key_path}")

    with open(public_key_path, "r") as f:
        public_key = f.read().strip()

    _run_terraform_command("apply", public_key=public_key, profile=profile, proxy_count=proxy_count)
    print("TREVORproxy serverless infrastructure deployment complete!")


def down(profile=None, proxy_count=None):
    """
    Destroys the TREVORproxy serverless infrastructure using Terraform.
    """
    print("Destroying TREVORproxy serverless infrastructure...")
    _run_terraform_command("destroy", public_key="", profile=profile, proxy_count=proxy_count)  # public_key is passed as empty for destroy
    print("TREVORproxy serverless infrastructure destruction complete!")


if __name__ == "__main__":
    # This block is for testing infra_manager.py directly
    if len(sys.argv) < 2:
        print("Usage: python infra_manager.py [up | down]")
        sys.exit(1)

    action = sys.argv[1]
    if action == "up":
        up()
    elif action == "down":
        down()
    else:
        print("Invalid action. Use 'up' or 'down'.")
        sys.exit(1)
