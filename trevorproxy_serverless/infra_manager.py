import os
import subprocess
import sys


def _run_terraform_command(command, public_key=None):
    """
    Helper function to run terraform commands.
    """
    tf_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "infra")

    # Check if terraform is installed
    try:
        subprocess.run(["terraform", "--version"], check=True, capture_output=True)
    except FileNotFoundError:
        print("Error: terraform command not found. Please ensure Terraform is installed and in your PATH.")
        sys.exit(1)

    # Initialize Terraform
    print(f"Initializing Terraform in {tf_dir}...")
    subprocess.run(["terraform", "-chdir", tf_dir, "init"], check=True)

    # Prepare variables for terraform apply/destroy
    tf_vars = []
    if public_key is not None:
        tf_vars.extend(["-var", f"public_key={public_key}"])
    else:  # For destroy, public_key is empty string
        tf_vars.extend(["-var", "public_key="])

    full_command = ["terraform", "-chdir", tf_dir, command, "-auto-approve"] + tf_vars
    print(f"Running: {' '.join(full_command)}")
    subprocess.run(full_command, check=True)


def up():
    """
    Deploys the TREVORproxy serverless infrastructure using Terraform.
    """
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

    _run_terraform_command("apply", public_key=public_key)
    print("TREVORproxy serverless infrastructure deployment complete!")


def down():
    """
    Destroys the TREVORproxy serverless infrastructure using Terraform.
    """
    print("Destroying TREVORproxy serverless infrastructure...")
    _run_terraform_command("destroy", public_key="")  # public_key is passed as empty for destroy
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
