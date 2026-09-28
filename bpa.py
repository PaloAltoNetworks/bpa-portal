import requests
import getpass
import json
import os
import gzip
import time

# --- Configuration & Auth Setup ---
TOKEN_URL = "https://auth.apps.paloaltonetworks.com/oauth2/access_token"
BASE_URL = "https://api.strata.paloaltonetworks.com"
INITIATE_UPLOAD_URL = f"{BASE_URL}/posture/checks/v1/reports/config-file-upload"

def extract_tsg_from_client_id(client_id):
    """Extracts just the numeric TSG ID from a Service Account string."""
    try:
        domain_part = client_id.split('@')[1]
        tsg_id = domain_part.split('.')[0]
        return tsg_id
    except IndexError:
        print("Error: Client ID format is invalid.")
        exit(1)

def get_oauth_token(client_id, client_secret):
    """Retrieves the JWT Access Token using Client Credentials flow."""
    tsg_id = extract_tsg_from_client_id(client_id)
    
    payload = {
        'grant_type': 'client_credentials',
        'scope': f'tsg_id:{tsg_id}'
    }
    
    print(f"\n[1/5] Requesting access token...")
    response = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data=payload
    )
    
    if response.status_code == 200:
        print("Success: Token retrieved.")
        return response.json().get("access_token")
    else:
        print(f"Error: Failed to get token. Status: {response.status_code}")
        exit(1)

def initiate_upload(token):
    """Step 1: Get the secure upload URL from the Posture API."""
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    print("[2/5] Initiating configuration upload session...")
    response = requests.post(INITIATE_UPLOAD_URL, headers=headers, json={})

    if response.status_code in [200, 201]:
        data = response.json()
        upload_url = data.get("upload_url")
        tracking_url = response.headers.get("Location")
        print("Success: Secured upload URL generated.")
        return upload_url, tracking_url
    else:
        print(f"Error: Initiation failed. Status: {response.status_code}")
        print(response.text)
        exit(1)

def execute_file_upload(upload_url, file_path):
    """Step 2: Upload the actual file using the secure URL."""
    if not os.path.exists(file_path):
        print(f"Error: File not found at {file_path}")
        exit(1)

    print(f"[3/5] Uploading {file_path} to secure cloud storage...")
    
    with open(file_path, 'rb') as f:
        xml_data = f.read()
    
    compressed_data = gzip.compress(xml_data)

    headers = {
        'Content-Type': 'plain/text',
        'Content-Encoding': 'gzip'
    }

    response = requests.put(upload_url, headers=headers, data=compressed_data)

    if response.status_code in [200, 201, 202]:
        print("Success: File uploaded for processing.")
        return True
    else:
        print(f"Error: File upload failed. Status: {response.status_code}")
        print(response.text)
        return False

def poll_and_download(token, tracking_uri):
    """Step 3: Poll the API, wait for completion, and download the final report."""
    print("\n[4/5] Polling for assessment results...")
    
    # Bug fix for missing /v1/ in the Location header
    if "/v1/" not in tracking_uri:
        tracking_uri = tracking_uri.replace("/reports/", "/v1/reports/")
    
    if tracking_uri.startswith("/"):
        poll_url = f"{BASE_URL}{tracking_uri}"
    else:
        poll_url = tracking_uri
        
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }

    attempt = 1
    while True:
        print(f"  -> Attempt {attempt}: Checking status...")
        response = requests.get(poll_url, headers=headers)
        
        if response.status_code == 202:
            print("     Status: Server accepted request. Waiting 20 seconds...")
            time.sleep(20)
            attempt += 1
            
        elif response.status_code == 200:
            data = response.json()
            job_status = data.get("status", "").upper()
            
            # Still working
            if job_status in ["PENDING", "RUNNING", "PROCESSING", "IN_PROGRESS", "UPLOAD_COMPLETE", "QUEUED"]:
                msg = data.get("message", "Working...")
                print(f"     Status: {job_status} - {msg}. Waiting 20 seconds...")
                time.sleep(20)
                attempt += 1
                
            # Job failed
            elif job_status in ["FAILED", "ERROR"]:
                print("\nError: Assessment job failed on the server.")
                print(json.dumps(data, indent=2))
                break
                
            # Job finished successfully
            elif job_status in ["COMPLETED", "SUCCESS"]:
                print("\n--- Assessment Processing Complete! ---")
                report_url = data.get("result", {}).get("report_url")
                
                if report_url:
                    print(f"[5/5] Downloading final BPA report from Google Cloud Storage...")
                    
                    # No auth headers needed for the pre-signed GCS URL
                    report_resp = requests.get(report_url)
                    
                    if report_resp.status_code == 200:
                        final_filename = "final_bpa_report.json"
                        with open(final_filename, "wb") as f:
                            f.write(report_resp.content)
                        print(f"\nSUCCESS! The comprehensive BPA report has been saved as: {final_filename}")
                    else:
                        print(f"Error downloading the report: HTTP {report_resp.status_code}")
                else:
                    print("Error: Job completed, but 'report_url' was not found in the response.")
                    print(json.dumps(data, indent=2))
                
                break
                
        else:
            print(f"Unexpected response during polling. Status: {response.status_code}")
            print(response.text)
            print("Will try again in 20 seconds...")
            time.sleep(20)
            attempt += 1

# --- Execution Flow ---
if __name__ == "__main__":
    print("--- Palo Alto Networks SCM BPA Automation ---")
    
    c_id = input("Enter Client ID: ").strip()
    c_secret = getpass.getpass("Enter Client Secret: ").strip()
    cfg_file = input("Enter path to your raw .xml config file: ").strip()

    # Step 1: Authenticate
    access_token = get_oauth_token(c_id, c_secret)
    
    # Step 2: Initiate and get URLs
    gcs_upload_url, job_tracking_url = initiate_upload(access_token)
    
    # Step 3: Upload the file
    if gcs_upload_url and job_tracking_url:
        upload_success = execute_file_upload(gcs_upload_url, cfg_file)
        
        # Step 4 & 5: Wait for results and download
        if upload_success:
            poll_and_download(access_token, job_tracking_url)