import requests
import logging
import urllib3
import json
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Disable SSL warnings since we're using verify=False for local network device
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Config (loaded from environment variables) ---
NPM_BASE = os.getenv("NPM_BASE")
NPM_USER = os.getenv("NPM_USER")
NPM_PASS = os.getenv("NPM_PASS")

UNIFI_BASE = os.getenv("UNIFI_BASE")
UNIFI_API_KEY = os.getenv("UNIFI_API_KEY")
UNIFI_SITE_ID = os.getenv("UNIFI_SITE_ID")

STATE_FILE = os.getenv("STATE_FILE", "npm_unifi_state.json")  # Default to npm_unifi_state.json if not set

# Validate required environment variables
required_vars = {
    "NPM_BASE": NPM_BASE,
    "NPM_USER": NPM_USER,
    "NPM_PASS": NPM_PASS,
    "UNIFI_BASE": UNIFI_BASE,
    "UNIFI_API_KEY": UNIFI_API_KEY,
    "UNIFI_SITE_ID": UNIFI_SITE_ID
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}. Please check your .env file.")

# --- State Management ---
def load_state():
    """Load the state file tracking managed DNS records"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                logger.info(f"Loaded state file: {len(state.get('managed_records', {}))} managed records")
                return state
        except Exception as e:
            logger.warning(f"Failed to load state file: {e}")
            return {"managed_records": {}}
    else:
        logger.info("No state file found, starting fresh")
        return {"managed_records": {}}

def save_state(state):
    """Save the state file"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        logger.debug(f"Saved state file: {len(state.get('managed_records', {}))} managed records")
    except Exception as e:
        logger.error(f"Failed to save state file: {e}")

# --- NPM: Get token ---
def npm_get_token():
    logger.info(f"Authenticating with NPM at {NPM_BASE}")
    r = requests.post(f"{NPM_BASE}/api/tokens", json={
        "identity": NPM_USER, "secret": NPM_PASS
    })
    logger.debug(f"NPM auth response status: {r.status_code}")
    if r.status_code != 200:
        logger.error(f"NPM auth failed: {r.text}")
        raise Exception(f"NPM authentication failed with status {r.status_code}")
    token = r.json()["token"]
    logger.info("Successfully authenticated with NPM")
    return token

# --- NPM: Get proxy hosts ---
def npm_get_hosts(token):
    logger.info("Fetching proxy hosts from NPM")
    r = requests.get(f"{NPM_BASE}/api/nginx/proxy-hosts", headers={
        "Authorization": f"Bearer {token}"
    })
    logger.debug(f"NPM hosts response status: {r.status_code}")
    if r.status_code != 200:
        logger.error(f"Failed to fetch NPM hosts: {r.text}")
        raise Exception(f"Failed to fetch NPM hosts with status {r.status_code}")

    hosts = []
    for host in r.json():
        for domain in host["domain_names"]:
            hosts.append({
                "domain": domain,
                "forward_host": host["forward_host"],
                "forward_port": host["forward_port"]
            })
    logger.info(f"Found {len(hosts)} proxy host domains")
    for host in hosts:
        logger.debug(f"  - {host['domain']} -> {host['forward_host']}:{host['forward_port']}")
    return hosts

# --- UniFi: Get API headers ---
def unifi_get_headers():
    """Returns headers for UniFi API requests with API key authentication"""
    return {
        "X-API-Key": UNIFI_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

# --- UniFi: Get site ID ---
def unifi_get_site_id():
    """Auto-discover the site ID from the UniFi controller"""
    logger.info("Attempting to auto-discover site ID from UniFi controller")

    # Try the Integration API
    logger.info("Trying Integration API endpoint for site info...")
    url = f"{UNIFI_BASE}/proxy/network/integration/v1/sites"
    logger.debug(f"Request URL: {url}")

    r = requests.get(url, headers=unifi_get_headers(), verify=False)
    logger.debug(f"UniFi sites response status: {r.status_code}")

    if r.status_code == 401:
        logger.error("401 Unauthorized - API key may not have permission")
        logger.error("Cannot auto-discover site ID")
        logger.error("Please find your site UUID manually and set UNIFI_SITE_ID")
        raise Exception("Cannot access sites endpoint - please hardcode UNIFI_SITE_ID with the UUID")

    logger.debug(f"UniFi sites response body: {r.text[:500]}")

    if r.status_code != 200:
        logger.error(f"Failed to fetch sites: {r.status_code} - {r.text}")
        raise Exception(f"Failed to fetch sites from UniFi controller (status {r.status_code})")

    if not r.text:
        logger.error("Empty response from UniFi API")
        raise Exception("Empty response when fetching sites")

    try:
        sites = r.json()
    except Exception as e:
        logger.error(f"Failed to parse JSON response: {e}")
        logger.error(f"Response text: {r.text}")
        raise

    if not sites:
        raise Exception("No sites found on UniFi controller")

    site_id = sites[0]['id']
    site_name = sites[0].get('name', 'Unknown')
    logger.info(f"Using site: {site_name} (ID: {site_id})")
    return site_id

# --- UniFi: Get existing DNS policies (with pagination) ---
def unifi_get_dns(site_id):
    logger.info("Fetching existing DNS policies from UniFi")
    all_records = []
    offset = 0
    limit = 100  # Fetch 100 records per page

    while True:
        url = f"{UNIFI_BASE}/proxy/network/integration/v1/sites/{site_id}/dns/policies?offset={offset}&limit={limit}"
        logger.debug(f"Fetching DNS policies: offset={offset}, limit={limit}")
        r = requests.get(url, headers=unifi_get_headers(), verify=False)
        logger.debug(f"UniFi get DNS response status: {r.status_code}")

        if r.status_code != 200:
            logger.error(f"Failed to fetch DNS policies: {r.status_code} - {r.text}")
            break

        data = r.json()

        # Check if data is a dict with a 'data' key (some APIs wrap responses)
        if isinstance(data, dict) and 'data' in data:
            data = data['data']

        if not data or len(data) == 0:
            # No more records
            break

        all_records.extend(data)
        logger.debug(f"Fetched {len(data)} records, total so far: {len(all_records)}")

        # If we got fewer records than the limit, we've reached the end
        if len(data) < limit:
            break

        offset += limit

    logger.info(f"Found {len(all_records)} total existing DNS policies")
    for record in all_records:
        if not isinstance(record, dict):
            logger.warning(f"Unexpected record type: {type(record)} - {record}")
            continue

        record_type = record.get('type')
        if record_type == 'A_RECORD':
            domain = record.get('domain', '')
            ip = record.get('ipv4Address', '')
            logger.debug(f"  - {domain} -> {ip} (A)")
        elif record_type == 'AAAA_RECORD':
            domain = record.get('domain', '')
            ipv6 = record.get('ipv6Address', '')
            logger.debug(f"  - {domain} -> {ipv6} (AAAA)")
        else:
            logger.debug(f"  - {record}")

    return all_records

# --- UniFi: Create a DNS policy (A record) ---
def unifi_create_record(site_id, domain, ip_address):
    payload = {
        "type": "A_RECORD",
        "enabled": True,
        "domain": domain,
        "ipv4Address": ip_address,
        "ttlSeconds": 300  # 5 minutes TTL
    }
    logger.debug(f"Creating DNS policy with payload: {payload}")

    url = f"{UNIFI_BASE}/proxy/network/integration/v1/sites/{site_id}/dns/policies"
    r = requests.post(url, headers=unifi_get_headers(), json=payload, verify=False)

    logger.debug(f"Create DNS policy response status: {r.status_code}")
    logger.debug(f"Create DNS policy response body: {r.text}")

    if r.status_code == 201:
        result = r.json()
        logger.info(f"✓ Successfully created DNS policy: {domain} -> {ip_address}")
        return result
    else:
        logger.error(f"✗ Failed to create DNS policy {domain}: {r.status_code} - {r.text}")
        return None

# --- UniFi: Update a DNS policy ---
def unifi_update_record(site_id, policy_id, domain, ip_address):
    payload = {
        "type": "A_RECORD",
        "enabled": True,
        "domain": domain,
        "ipv4Address": ip_address,
        "ttlSeconds": 300  # 5 minutes TTL
    }
    logger.debug(f"Updating DNS policy with payload: {payload}")

    url = f"{UNIFI_BASE}/proxy/network/integration/v1/sites/{site_id}/dns/policies/{policy_id}"
    r = requests.patch(url, headers=unifi_get_headers(), json=payload, verify=False)

    logger.debug(f"Update DNS policy response status: {r.status_code}")
    logger.debug(f"Update DNS policy response body: {r.text}")

    if r.status_code == 200:
        logger.info(f"✓ Successfully updated DNS policy: {domain} -> {ip_address}")
        return True
    else:
        logger.error(f"✗ Failed to update DNS policy {domain}: {r.status_code} - {r.text}")
        return False

# --- UniFi: Delete a DNS policy ---
def unifi_delete_record(site_id, policy_id, domain):
    logger.debug(f"Deleting DNS policy: {domain} (ID: {policy_id})")

    url = f"{UNIFI_BASE}/proxy/network/integration/v1/sites/{site_id}/dns/policies/{policy_id}"
    r = requests.delete(url, headers=unifi_get_headers(), verify=False)

    logger.debug(f"Delete DNS policy response status: {r.status_code}")

    if r.status_code in [200, 204]:
        logger.info(f"✓ Successfully deleted DNS policy: {domain}")
        return True
    else:
        logger.error(f"✗ Failed to delete DNS policy {domain}: {r.status_code} - {r.text}")
        return False

# --- Main ---
def main():
    logger.info("=" * 60)
    logger.info("Starting NPM to UniFi DNS sync")
    logger.info("=" * 60)

    try:
        # Load state file
        state = load_state()
        managed_records = state.get("managed_records", {})

        # Get proxy hosts from NPM
        npm_token = npm_get_token()
        proxy_hosts = npm_get_hosts(npm_token)

        # Build set of current NPM domains
        npm_domains = {host["domain"]: host["forward_host"] for host in proxy_hosts}

        # Get UniFi site ID (hardcoded or auto-discover)
        if UNIFI_SITE_ID:
            logger.info(f"Using hardcoded site ID: {UNIFI_SITE_ID}")
            site_id = UNIFI_SITE_ID
        else:
            site_id = unifi_get_site_id()

        # Get existing DNS policies from UniFi
        existing_dns = unifi_get_dns(site_id)

        # Build a map of existing domains to their policy IDs and IP addresses
        existing_domains = {}
        for record in existing_dns:
            if not isinstance(record, dict):
                logger.warning(f"Skipping non-dict record: {type(record)} - {record}")
                continue
            record_type = record.get('type')
            if record_type == 'A_RECORD':
                domain = record.get('domain', '')
                policy_id = record.get('id')
                ip_address = record.get('ipv4Address', '')
                if domain and policy_id:
                    existing_domains[domain] = {
                        "policy_id": policy_id,
                        "ip_address": ip_address
                    }
                    logger.debug(f"Mapped existing domain: {domain} -> {ip_address} (ID: {policy_id})")

        logger.info(f"Total existing A records mapped: {len(existing_domains)}")
        logger.debug(f"Existing domains: {list(existing_domains.keys())}")

        logger.info("=" * 60)
        logger.info("Processing proxy hosts")
        logger.info("=" * 60)

        created_count = 0
        updated_count = 0
        skipped_count = 0
        failed_count = 0

        # Create new records or update existing ones for NPM domains
        for domain, target_ip in npm_domains.items():
            if domain not in existing_domains:
                # Domain doesn't exist in UniFi - create it
                logger.info(f"Creating DNS policy: {domain} -> {target_ip}")
                result = unifi_create_record(site_id, domain, target_ip)
                if result:
                    # Track this record in our state file
                    policy_id = result.get('id')
                    if policy_id:
                        managed_records[domain] = {
                            "policy_id": policy_id,
                            "ip_address": target_ip
                        }
                    created_count += 1
                else:
                    # Check if it failed because record already exists
                    # This can happen if the record exists but we didn't detect it
                    # We'll just skip it and add it to managed records
                    logger.warning(f"Failed to create {domain}, may already exist - adding to managed records")
                    # Try to find this domain in UniFi again
                    if domain in existing_domains:
                        managed_records[domain] = {
                            "policy_id": existing_domains[domain]["policy_id"],
                            "ip_address": target_ip
                        }
                        skipped_count += 1
                    else:
                        failed_count += 1
            else:
                # Domain exists in UniFi - check if IP changed
                existing_info = existing_domains[domain]
                existing_ip = existing_info["ip_address"]
                policy_id = existing_info["policy_id"]

                if existing_ip != target_ip:
                    # IP address changed - update the record
                    logger.info(f"IP changed for {domain}: {existing_ip} -> {target_ip}")
                    if unifi_update_record(site_id, policy_id, domain, target_ip):
                        # Update our state with the new IP
                        managed_records[domain] = {
                            "policy_id": policy_id,
                            "ip_address": target_ip
                        }
                        updated_count += 1
                    else:
                        logger.error(f"Failed to update {domain}")
                        failed_count += 1
                else:
                    # IP is the same, no changes needed
                    logger.info(f"No changes needed: {domain} -> {existing_ip}")
                    # Make sure it's tracked in our state if it exists
                    if domain not in managed_records:
                        managed_records[domain] = {
                            "policy_id": policy_id,
                            "ip_address": target_ip
                        }
                    skipped_count += 1

        logger.info("=" * 60)
        logger.info("Cleaning up removed domains")
        logger.info("=" * 60)

        deleted_count = 0
        # Delete records that are no longer in NPM but are managed by us
        domains_to_delete = []
        for domain, record_info in managed_records.items():
            if domain not in npm_domains:
                policy_id = record_info.get("policy_id")
                if policy_id:
                    logger.info(f"Deleting DNS policy (no longer in NPM): {domain}")
                    if unifi_delete_record(site_id, policy_id, domain):
                        domains_to_delete.append(domain)
                        deleted_count += 1

        # Remove deleted domains from state
        for domain in domains_to_delete:
            del managed_records[domain]

        # Save updated state
        state["managed_records"] = managed_records
        save_state(state)

        logger.info("=" * 60)
        logger.info("Summary")
        logger.info("=" * 60)
        logger.info(f"Total NPM domains: {len(proxy_hosts)}")
        logger.info(f"Created: {created_count}")
        logger.info(f"Updated (IP changed): {updated_count}")
        logger.info(f"Skipped (no changes): {skipped_count}")
        logger.info(f"Deleted (no longer in NPM): {deleted_count}")
        logger.info(f"Failed: {failed_count}")
        logger.info(f"Total managed records: {len(managed_records)}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Error during execution: {e}", exc_info=True)

if __name__ == "__main__":
    main()