import datetime
import json
import logging
import os

import oci.usage_api.models
import requests
from oci.usage_api.models import RequestSummarizedUsagesDetails


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.handlers = [handler]

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
HEALTHCHECKS_URL = os.environ["HEALTHCHECKS_URL_OCI_USAGE_COST"]
N8N_WEBHOOK_URL = os.environ["N8N_WEBHOOK_URL"]
N8N_CREDENTIALS = os.environ["N8N_CREDENTIALS"]
THRESHOLD = float(os.environ["THRESHOLD"])

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


def send_discord_notification(message) -> dict:
    """Send a Discord notification."""
    data = {"content": message}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL, json=data, headers=headers, timeout=15
        )
        response.raise_for_status()
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.error(f"Failed to send Discord notification. Exception: {exception}")
        return {"ok": False, "status": "Failed"}


def send_n8n_notification(total, quantity) -> dict:
    payload = {
        "title": "OCI Usage Cost",
        "message": f"Total computed amount: {total}\nTotal computed quantity: {quantity}",
    }
    try:
        response = requests.post(
            N8N_WEBHOOK_URL,
            headers={
                "Authorization": f"Basic {N8N_CREDENTIALS}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        logger.info(
            json.dumps(
                {"event": "n8n_notification_sent", "status": response.status_code}
            )
        )
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.error(
            json.dumps({"event": "n8n_notification_failed", "error": str(exception)})
        )
        return {"ok": False, "status": "Failed"}


def send_discord_if_threshold_exceeded(total_computed_amount: float) -> bool:
    if total_computed_amount > THRESHOLD:
        message = f"ATTENTION! OCI costs of {total_computed_amount:.2f} USD exceeds {THRESHOLD} USD!"
        discord_response = send_discord_notification(message)
        if discord_response["ok"]:
            logger.info(
                json.dumps(
                    {
                        "event": "discord_notification_sent",
                        "amount": total_computed_amount,
                    }
                )
            )
            return True
        else:
            logger.error(
                json.dumps(
                    {
                        "event": "discord_notification_failed",
                        "amount": total_computed_amount,
                    }
                )
            )
            return False
    return False


def main() -> None:
    # Get the total cost for this month
    (total_computed_amount, total_computed_quantity) = get_usage_totals()

    # Get the total cost for this month, grouped by service
    (
        total_computed_amounts_by_service,
        total_computed_quantities_by_service,
    ) = get_usage_totals_by_service()

    logger.info(
        json.dumps(
            {
                "event": "usage_totals",
                "timestamp": datetime.datetime.now().isoformat(),
                "total_computed_amount": total_computed_amount,
                "total_computed_quantity": total_computed_quantity,
                "amounts_by_service": total_computed_amounts_by_service,
                "quantities_by_service": total_computed_quantities_by_service,
            }
        )
    )

    for service, amount in total_computed_amounts_by_service.items():
        logger.info(
            json.dumps(
                {
                    "event": "service_usage",
                    "service": service,
                    "total_computed_amount": amount,
                    "total_computed_quantity": total_computed_quantities_by_service[
                        service
                    ],
                }
            )
        )

    # Always send to n8n
    send_n8n_notification(total_computed_amount, total_computed_quantity)

    # Only send to Discord if threshold is exceeded
    if not send_discord_if_threshold_exceeded(total_computed_amount):
        logger.info(
            json.dumps(
                {"event": "threshold_not_exceeded", "amount": total_computed_amount}
            )
        )

    try:
        requests.get(HEALTHCHECKS_URL, timeout=10)
        logger.info(json.dumps({"event": "healthcheck_ping_success"}))
    except requests.RequestException as re:
        logger.error(json.dumps({"event": "healthcheck_ping_failed", "error": str(re)}))


if __name__ == "__main__":
    main()
