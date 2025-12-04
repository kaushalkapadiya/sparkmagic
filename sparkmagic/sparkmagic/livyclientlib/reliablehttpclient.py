# Copyright (c) 2015  aggftw@gmail.com
# Distributed under the terms of the Modified BSD License.
import json
from time import sleep
import requests
import sparkmagic.utils.configuration as conf
from sparkmagic.utils.sparklogger import SparkLog
from .exceptions import HttpClientException, HttpSessionAdapterConfigException
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import importlib


class ReliableHttpClient(object):
    """Http client that is reliable in its requests. Uses requests library."""

    def __init__(self, endpoint, headers, retry_policy):
        self._endpoint = endpoint
        self._headers = headers
        self._retry_policy = retry_policy
        self._auth = self._endpoint.auth
        self._session = requests.Session()
        self.logger = SparkLog("ReliableHttpClient")
        self.verify_ssl = not conf.ignore_ssl_errors()
        if not self.verify_ssl:
            self.logger.debug(
                "ATTENTION: Will ignore SSL errors. This might render you vulnerable to attacks."
            )
            requests.packages.urllib3.disable_warnings()
        self._set_http_session_config()

    def _set_http_session_config(self):
        http_session_config = conf.http_session_config()
        if http_session_config and http_session_config.get("adapters"):
            self._set_http_session_adapters(http_session_config["adapters"])

    def _set_http_session_adapters(self, adapters):
        for adapter in adapters:
            full_class = adapter.get("adapter")
            adapter_prefix = adapter.get("prefix")
            if full_class is None or adapter_prefix is None:
                raise HttpSessionAdapterConfigException(
                    "Invalid http session adapter config, prefix: {} or class: {} "
                    "not defined correctly".format(adapter_prefix, full_class)
                )
            module, class_name = full_class.rsplit(".", 1)
            adapter_module = importlib.import_module(module)
            adapter_class = getattr(adapter_module, class_name)
            self._session.mount(adapter_prefix, adapter_class())

    def get_headers(self):
        return self._headers

    def compose_url(self, relative_url: str):
        """
        Composes a full URL by appending the relative path to the base URL's path
        and merging query parameters.
        
        Args:
            self: The client instance (expected to have self._endpoint.url).
            relative_url (str): The relative URL or path (e.g., 'resource?id=1').
            
        Returns:
            str: The combined URL.
        """
        # Access the base URL from the instance as in the original function
        base_url = self._endpoint.url
        
        base = urlparse(base_url)
        rel = urlparse(relative_url)
    
        # 1. Combine Paths
        # Append relative path to base path, ensuring single slash separator
        base_path = base.path.rstrip('/')
        rel_path = rel.path.lstrip('/')
        
        if base_path and rel_path:
            new_path = f"{base_path}/{rel_path}"
        else:
            new_path = base_path or rel_path
            # Preserve root slash if base was just root and no relative path
            if not new_path and base.path == '/':
                new_path = '/'
    
        # 2. Merge Query Parameters
        # Parse both query strings, combine them (Base + Relative), and re-encode
        # This handles special characters and ensures valid query strings
        combined_qs = parse_qsl(base.query) + parse_qsl(rel.query)
        new_query = urlencode(combined_qs)
    
        # 3. Reconstruct URL
        return urlunparse((
            base.scheme,
            base.netloc,
            new_path,
            base.params,
            new_query,
            rel.fragment or base.fragment
        ))
        
    def get(self, relative_url, accepted_status_codes):
        """Sends a get request. Returns a response."""
        return self._send_request(
            relative_url, accepted_status_codes, self._session.get
        )

    def post(self, relative_url, accepted_status_codes, data):
        """Sends a post request. Returns a response."""
        return self._send_request(
            relative_url, accepted_status_codes, self._session.post, data
        )

    def delete(self, relative_url, accepted_status_codes):
        """Sends a delete request. Returns a response."""
        return self._send_request(
            relative_url, accepted_status_codes, self._session.delete
        )

    def _send_request(self, relative_url, accepted_status_codes, function, data=None):
        return self._send_request_helper(
            self.compose_url(relative_url), accepted_status_codes, function, data, 0
        )

    def _send_request_helper(
        self, url, accepted_status_codes, function, data, retry_count
    ):
        while True:
            try:
                if data is None:
                    r = function(
                        url,
                        headers=self._headers,
                        auth=self._auth,
                        verify=self.verify_ssl,
                    )
                else:
                    r = function(
                        url,
                        headers=self._headers,
                        auth=self._auth,
                        data=json.dumps(data),
                        verify=self.verify_ssl,
                    )
            except requests.exceptions.RequestException as e:
                error = True
                r = None
                status = None
                text = None

                self.logger.error("Request to '{}' failed with '{}'".format(url, e))
            else:
                error = False
                status = r.status_code
                text = r.text

            if error or status not in accepted_status_codes:
                if self._retry_policy.should_retry(status, error, retry_count):
                    sleep(self._retry_policy.seconds_to_sleep(retry_count))
                    retry_count += 1
                    continue

                if error:
                    raise HttpClientException(
                        "Error sending http request and maximum retry encountered."
                    )
                else:
                    raise HttpClientException(
                        "Invalid status code '{}' from {} with error payload: {}".format(
                            status, url, text
                        )
                    )
            return r
