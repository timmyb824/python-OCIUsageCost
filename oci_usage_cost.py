import datetime
import logging
import os

import oci.usage_api.models
import requests
from gotify import Gotify
from oci.usage_api.models import RequestSummarizedUsagesDetails
from rocketry import Rocketry
from rocketry.conds import every  # daily

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)

app = Rocketry()

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
HEALTHCHECKS_URL = os.environ["HEALTHCHECKS_URL_OCI_USAGE_COST"]
THRESHOLD = float(os.environ["THRESHOLD"])
GOTIFY = Gotify(
    base_url=os.environ["GOTIFY_HOST"],
    app_token=os.environ["GOTIFY_TOKEN_ADHOC_SCRIPTS"],
)
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
NTFY_ACCESS_TOKEN = os.environ["NTFY_ACCESS_TOKEN"]
NTFY_URL = f"https://ntfy.timmybtech.com/{NTFY_TOPIC}"
INTERVAL_MINS = os.environ["INTERVAL_MINS"]

CONFIG_PATH = os.path.expanduser("~/.oci/config")
if not os.path.exists(CONFIG_PATH):
    CONFIG_PATH = "/scripts/config"
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError("OCI config file not found")

config = oci.config.from_file(CONFIG_PATH)

# Create a usage api client
usage_api_client = oci.usage_api.UsageapiClient(config)

# Get the tenant ID
tenant_id = config["tenancy"]

# Get the start date and end date for the current month
today = datetime.date.today()
start_date = datetime.date(today.year, today.month, 1)
end_date = today + datetime.timedelta(days=1)


def get_usage_totals() -> tuple:
    # Query the usage API for the total cost for this month
    usage_request = RequestSummarizedUsagesDetails(
        tenant_id=tenant_id,
        time_usage_started=start_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        time_usage_ended=end_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        granularity="DAILY",
        query_type="COST",
    )

    usage_response = usage_api_client.request_summarized_usages(usage_request)

    # Calculate the total computed amount and quantity for all services
    total_computed_amount = 0.0
    total_computed_quantity = 0.0

    items = usage_response.data.items

    for item in items:
        if item.computed_amount is not None:
            total_computed_amount += item.computed_amount
        if item.computed_quantity is not None:
            total_computed_quantity += item.computed_quantity

    return (total_computed_amount, total_computed_quantity)


def get_usage_totals_by_service() -> tuple:
    # Query the usage API for the total cost for this month, grouped by service
    usage_request = RequestSummarizedUsagesDetails(
        tenant_id=tenant_id,
        time_usage_started=start_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        time_usage_ended=end_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        granularity="DAILY",
        query_type="COST",
        group_by=["service"],
    )

    usage_response = usage_api_client.request_summarized_usages(usage_request)

    # Calculate the total computed amount and quantity for each service
    total_computed_amounts_by_service = {}
    total_computed_quantities_by_service = {}

    items = usage_response.data.items

    for item in items:
        if item.service not in total_computed_amounts_by_service:
            total_computed_amounts_by_service[item.service] = 0.0
        if item.service not in total_computed_quantities_by_service:
            total_computed_quantities_by_service[item.service] = 0.0

        if item.computed_amount is not None:
            total_computed_amounts_by_service[item.service] += item.computed_amount
        if item.computed_quantity is not None:
            total_computed_quantities_by_service[item.service] += item.computed_quantity

    return (total_computed_amounts_by_service, total_computed_quantities_by_service)


def send_gotify_notification(message) -> dict:
    try:
        return GOTIFY.create_message(
            title="OCI Cost Alert",
            message=message,
            priority=5,
            extras={"client::display": {"contentType": "text/markdown"}},
        )
    except Exception as exception:
        logger.error(f"Failed to send Gotify notification. Exception: {exception}")
        return {}


def send_discord_notification(message) -> dict:
    data = {"content": message}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL, json=data, headers=headers, timeout=15
        )
        headers = {"Authorization": f"Basic {NTFY_ACCESS_TOKEN}"}
        response.raise_for_status()
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.error(f"Failed to send Discord notification. Exception: {exception}")
        return {"ok": False, "status": "Failed"}


def send_ntfy_notification(message) -> dict:
    try:
        response = requests.post(
            NTFY_URL,
            headers={"Authorization": f"Bearer {NTFY_ACCESS_TOKEN}"},
            data=message,
            timeout=15,
        )
        response.raise_for_status()
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.error(f"Failed to send Ntfy notification. Exception: {exception}")
        return {"ok": False, "status": "Failed"}


def check_threshold_exceeded(total_computed_amount: float) -> bool:
    # sourcery skip: extract-duplicate-method, last-if-guard
    if total_computed_amount > THRESHOLD:
        message = f"ATTENTION! OCI costs of {total_computed_amount:.2f} USD exceeds {THRESHOLD} USD!"
        discord_response = send_discord_notification(message)
        gotify_response = send_gotify_notification(message)
        ntfy_response = send_ntfy_notification(message)
        if discord_response["ok"] and gotify_response["id"] and ntfy_response["ok"]:
            print("\nDiscord, Gotify, and Ntfy notifications sent successfully.\n")
            print("###############################################\n")
            return True
        elif discord_response["ok"]:
            print("\Discord notification sent successfully.\n")
            print("###############################################\n")
            return True
        elif gotify_response["id"]:
            print("\nGotify notification sent successfully.\n")
            print("###############################################\n")
            return True
        elif ntfy_response["ok"]:
            print("\nNtfy notification sent successfully.\n")
            print("###############################################\n")
            return True
        else:
            logger.error("Failed to send notifications.\n")
            return False


# @app.task(daily.at("22:30"))
# @app.task(every("24 hours"))
@app.task(every(f"{INTERVAL_MINS} minutes"))
def main() -> None:
    # Get the total cost for this month
    (total_computed_amount, total_computed_quantity) = get_usage_totals()

    # Get the total cost for this month, grouped by service
    (
        total_computed_amounts_by_service,
        total_computed_quantities_by_service,
    ) = get_usage_totals_by_service()
    print(f"Current date and time: {datetime.datetime.now()}")
    print(f"Total cost for this month: {str(total_computed_amount)}")
    print(f"Total quantity for this month: {str(total_computed_quantity)}")

    for service, amount in total_computed_amounts_by_service.items():
        print(f"\nFor service {service}:")
        print(f"\tTotal Computed Amount: {amount}")
        print(
            f"\tTotal Computed Quantity: {total_computed_quantities_by_service[service]}"
        )

    if not check_threshold_exceeded(total_computed_amount):
        print("\nNo threshold exceeded.\n")
        print("###############################################\n")
    try:
        requests.get(HEALTHCHECKS_URL, timeout=10)
    except requests.RequestException as re:
        logger.error(f"Failed to send health check signal. Exception: {re}\n")
    return


if __name__ == "__main__":
    app.run()
