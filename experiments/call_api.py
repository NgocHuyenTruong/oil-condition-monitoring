"""Talk to the mock lab API by hand, to see what an API call really returns.

First start the API in another terminal:   py -m oilmon.mock_api
Then:
    py experiments/call_api.py                 # first page, normal key
    py experiments/call_api.py --page 3        # another page
    py experiments/call_api.py --bad-key       # see a 401 error
    py experiments/call_api.py --since 2026-09-01T00:00:00   # only records changed after this time
"""
import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from oilmon import config  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--page", type=int, default=1)
parser.add_argument("--page-size", type=int, default=5)
parser.add_argument("--since", help="modified_since timestamp, e.g. 2026-09-01T00:00:00")
parser.add_argument("--bad-key", action="store_true", help="send a wrong API key")
args = parser.parse_args()

params = {"page": args.page, "page_size": args.page_size}
if args.since:
    params["modified_since"] = args.since
headers = {"X-Api-Key": "wrong-key" if args.bad_key else config.API_KEY}

try:
    resp = requests.get(f"{config.API_BASE_URL}/v1/samples", params=params, headers=headers, timeout=10)
except requests.ConnectionError:
    sys.exit("Cannot connect. Start the API first in another terminal:  py -m oilmon.mock_api")

print("Request :", resp.url)
print("Status  :", resp.status_code)
body = resp.json()
if resp.status_code != 200:
    print("Body    :", body)
    sys.exit()
print("Meta    :", body["meta"])
print(f"Records : {len(body['data'])} on this page\n")
first = body["data"][0]
print("First record (results shortened to 3 tests):")
print(json.dumps({**first, "results": first["results"][:3]}, indent=2))
