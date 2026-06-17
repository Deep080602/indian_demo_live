# ALGO_PULSE Terraform Automated AWS Deployment

This folder contains Terraform configuration files to automatically set up, configure, and boot your **ALGO_PULSE** server on AWS EC2 (Free Tier).

## Prerequisites (One-time setup on your local computer)

1. **Install Terraform**:
   - Download the Windows zip from the official [Terraform website](https://developer.hashicorp.com/terraform/downloads).
   - Extract the `terraform.exe` file and add the directory path to your Windows environment variables (PATH).
   
2. **Install the AWS CLI**:
   - Download and run the [AWS CLI MSI Installer for Windows](https://awscli.amazonaws.com/AWSCLIV2.msi).
   
3. **Configure AWS Credentials**:
   - In your AWS console, search for **IAM** (Identity and Access Management).
   - Go to **Users** -> Click on your user -> **Security credentials** tab.
   - Scroll down to **Access keys** and click **Create access key** (select "Command Line Interface (CLI)").
   - Download the `.csv` file containing your **Access Key ID** and **Secret Access Key**.
   - Open Command Prompt or PowerShell on your computer and run:
     ```cmd
     aws configure
     ```
   - Input your keys:
     - `AWS Access Key ID [None]:` *Paste your Access Key ID*
     - `AWS Secret Access Key [None]:` *Paste your Secret Access Key*
     - `Default region name [None]:` `ap-south-1` *(Mumbai is recommended for NSE trading)*
     - `Default output format [None]:` `json`

4. **Create an EC2 Key Pair** (if you haven't already):
   - In your AWS console, search for **EC2** -> Go to **Key Pairs** (under Network & Security) -> **Create key pair**.
   - Name it `algopulse-key`, format: `.pem`, and click **Create key pair** (this will download `algopulse-key.pem`).
   - Store it somewhere safe on your local drive.

---

## Deployment Steps

1. Open PowerShell or Command Prompt, and navigate to this folder:
   ```cmd
   cd "d:\Antigravity project\ALGO_PULSE\ALGO_PULSE\deploy\terraform"
   ```

2. Initialize Terraform (this installs the AWS provider):
   ```cmd
   terraform init
   ```

3. (Optional) Customize variables:
   - You can edit [variables.tf](file:///d:/Antigravity%20project/ALGO_PULSE/ALGO_PULSE/deploy/terraform/variables.tf) to update your GitHub repository URL (`github_repo`), key pair name (`key_name`), or region.

4. Apply the configuration to launch the server:
   ```cmd
   terraform apply
   ```
   - Review the planned actions and type `yes` to confirm.

5. After completion (about 2–3 minutes), Terraform will display the output:
   ```text
   server_public_ip = "54.xxx.xxx.xxx"
   dashboard_url = "http://54.xxx.xxx.xxx"
   dashboard_direct_url = "http://54.xxx.xxx.xxx:8000"
   ssh_command = "ssh -i ... ubuntu@54.xxx.xxx.xxx"
   ```

You can now visit the `dashboard_url` to see your online website!
If you ever want to destroy the resources and shut down the server to avoid charges, run:
```cmd
terraform destroy
```
