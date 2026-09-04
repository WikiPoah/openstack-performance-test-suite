from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import math
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from openstack_perf.results import (
    AssertionResult,
    FunctionalVerdict,
    ScenarioObservation,
    TimingSample,
)
from openstack_perf.statistics import calculate_timing_statistics


DEFAULT_SAMPLE_COUNT = 10
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProductValidationError(ValueError):
    """Raised for product validation failures whose text is safe to retain."""


def product_failure_message(context: str, error: Exception) -> str:
    """Describe a product failure without retaining external exception text."""
    if isinstance(error, ProductValidationError):
        return f"{context} failed: {error}"
    return f"{context} failed: {type(error).__name__}"


@dataclass(frozen=True)
class _HttpTarget:
    scenario_id: str
    target_id: str
    name: str
    url: str
    checks: Mapping[str, Callable[[bytes, str, str | None], bool]]


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, approved_urls: Collection[str]):
        super().__init__()
        self._approved_destinations = {
            _url_key(url, reject_credentials=True) for url in approved_urls
        }

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            source_origin = _origin(req.full_url, reject_credentials=True)
            destination_origin = _origin(newurl, reject_credentials=True)
            destination = _url_key(newurl, reject_credentials=True)
        except ValueError:
            raise ProductValidationError(
                "redirect destination was not approved"
            ) from None
        if (
            source_origin != destination_origin
            or destination not in self._approved_destinations
        ):
            raise ProductValidationError("redirect destination was not approved")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def observe_corporate_web_application(
    frontend_base_url: str,
    *,
    expected_release_title: str,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    opener=None,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[ScenarioObservation, ...]:
    """Observe the configured WordPress and static-site consumer contract."""
    base_url = _validated_base_url(frontend_base_url)
    if not expected_release_title:
        raise ValueError("expected_release_title must be non-empty")
    required_paths = (
        "/site/",
        "/site/about.html",
        "/site/products.html",
        "/site/team.html",
        "/site/contact.html",
    )

    def navigation_check(
        body: bytes, final_url: str, content_type: str | None
    ) -> bool:
        parser = _LinkParser()
        parser.feed(_decode(body))
        resolved_paths = {
            urlsplit(urljoin(final_url, link)).path
            for link in parser.links
            if _origin(urljoin(final_url, link)) == _origin(final_url)
        }
        return set(required_paths).issubset(resolved_paths)

    targets = (
        _text_target(
            "product.wordpress",
            "wordpress.home",
            "WordPress home",
            base_url,
            "/",
            "Corp Intranet",
        ),
        _text_target(
            "product.wordpress",
            "wordpress.search.release",
            "WordPress release search",
            base_url,
            "/?s=release",
            expected_release_title,
        ),
        _HttpTarget(
            "product.wordpress",
            "wordpress.rest.posts",
            "WordPress posts API",
            _target_url(base_url, "/?rest_route=/wp/v2/posts"),
            {
                "content_type": _is_json_content_type,
                "json_list": _json_list,
                "release_title": lambda body, _url, _content_type: _posts_contain_title(
                    body, expected_release_title
                ),
            },
        ),
        _text_target(
            "product.wordpress",
            "wordpress.login",
            "WordPress login page",
            base_url,
            "/wp-login.php",
            "loginform",
        ),
        _HttpTarget(
            "product.static_site",
            "static.home",
            "Static site home",
            _target_url(base_url, "/site/"),
            {
                "marker": _contains_marker("Home"),
                "navigation": navigation_check,
            },
        ),
        _text_target(
            "product.static_site", "static.about", "Static site about page",
            base_url, "/site/about.html", "About",
        ),
        _text_target(
            "product.static_site", "static.products", "Static site products page",
            base_url, "/site/products.html", "Products",
        ),
        _text_target(
            "product.static_site", "static.team", "Static site team page",
            base_url, "/site/team.html", "Team",
        ),
        _text_target(
            "product.static_site", "static.contact", "Static site contact page",
            base_url, "/site/contact.html", "Contact",
        ),
    )
    approved_urls = tuple(target.url for target in targets)
    client = opener or _build_product_opener(approved_urls)
    return tuple(
        _observe_target(
            target,
            approved_urls=approved_urls,
            sample_count=sample_count,
            timeout_seconds=timeout_seconds,
            opener=client,
            clock=clock,
        )
        for target in targets
    )


def observe_service_http_endpoints(
    frontend_base_url: str,
    tomcat_base_url: str,
    *,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    opener=None,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[ScenarioObservation, ...]:
    """Observe the configured nginx and Tomcat read-only HTTP endpoints."""
    frontend = _validated_base_url(frontend_base_url)
    tomcat = _validated_base_url(tomcat_base_url)
    targets = (
        _text_target(
            "product.service_http", "nginx.status", "nginx status",
            frontend, "/status", "Active connections:",
        ),
        _text_target(
            "product.service_http", "tomcat.home", "Tomcat home",
            tomcat, "/", "Corp Tomcat",
        ),
        _text_target(
            "product.service_http", "tomcat.examples", "Tomcat examples",
            tomcat, "/examples/", "Apache Tomcat Examples",
        ),
        _text_target(
            "product.service_http",
            "tomcat.hello_world",
            "Tomcat Hello World example",
            tomcat,
            "/examples/servlets/servlet/HelloWorldExample",
            "Hello World!",
        ),
    )
    approved_urls = tuple(target.url for target in targets)
    client = opener or _build_product_opener(approved_urls)
    return tuple(
        _observe_target(
            target,
            approved_urls=approved_urls,
            sample_count=sample_count,
            timeout_seconds=timeout_seconds,
            opener=client,
            clock=clock,
        )
        for target in targets
    )


def _text_target(scenario_id, target_id, name, base_url, path, marker):
    return _HttpTarget(
        scenario_id,
        target_id,
        name,
        _target_url(base_url, path),
        {"marker": _contains_marker(marker)},
    )


def _observe_target(
    target, *, approved_urls, sample_count, timeout_seconds, opener, clock
):
    _require_positive_integer(sample_count, "sample_count")
    _require_http_timeout(timeout_seconds)
    client = opener
    assertion_ids = ("status", *target.checks)
    aggregate = {assertion_id: True for assertion_id in assertion_ids}

    try:
        status, final_url, content_type, body, _ = _fetch(
            client, target.url, timeout_seconds, MAX_RESPONSE_BYTES, None
        )
        _validate_response(
            target,
            approved_urls,
            status,
            final_url,
            content_type,
            body,
            aggregate,
        )
    except Exception as exc:
        error = product_failure_message(f"{target.target_id} warm-up", exc)
        return _failed_observation(target, (), aggregate, error)

    samples = []
    errors = []
    for sequence in range(1, sample_count + 1):
        try:
            status, final_url, content_type, body, duration = _fetch(
                client, target.url, timeout_seconds, MAX_RESPONSE_BYTES, clock
            )
        except Exception as exc:
            error = product_failure_message(f"{target.target_id} request", exc)
            samples.append(TimingSample(sequence, _failure_duration(exc), False, error))
            errors.append(f"sample {sequence}: {error}")
            continue
        try:
            _validate_response(
                target,
                approved_urls,
                status,
                final_url,
                content_type,
                body,
                aggregate,
            )
        except Exception as exc:
            error = product_failure_message(f"{target.target_id} request", exc)
            samples.append(TimingSample(sequence, duration, False, error))
            errors.append(f"sample {sequence}: {error}")
        else:
            samples.append(TimingSample(sequence, duration))

    if errors:
        return _failed_observation(target, tuple(samples), aggregate, "; ".join(errors))
    return _observation(target, tuple(samples), aggregate, None)


def _fetch(opener, url, timeout, max_bytes, clock):
    request = Request(url, method="GET")
    start = clock() if clock else None
    stop = None
    try:
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(max_bytes + 1)
            stop = clock() if clock else None
            status = response.getcode()
            final_url = response.geturl()
            content_type = _response_content_type(response)
    except Exception as exc:
        if clock and stop is None:
            stop = clock()
        if clock:
            setattr(exc, "_openstack_perf_duration", max(0.0, stop - start))
        raise
    if len(body) > max_bytes:
        error = ProductValidationError("response exceeded the 2 MiB limit")
        if clock:
            setattr(error, "_openstack_perf_duration", max(0.0, stop - start))
        raise error
    return (
        status,
        final_url,
        content_type,
        body,
        None if clock is None else max(0.0, stop - start),
    )


def _validate_response(
    target, approved_urls, status, final_url, content_type, body, aggregate
):
    try:
        final_destination = _url_key(final_url, reject_credentials=True)
    except ValueError:
        raise ProductValidationError(
            "response destination was not approved"
        ) from None
    if (
        _origin(target.url, reject_credentials=True)
        != _origin(final_url, reject_credentials=True)
        or final_destination not in {
            _url_key(url, reject_credentials=True) for url in approved_urls
        }
    ):
        raise ProductValidationError("response destination was not approved")
    status_ok = status == 200
    aggregate["status"] &= status_ok
    if not status_ok:
        for assertion_id in target.checks:
            aggregate[assertion_id] = False
        raise ProductValidationError("expected HTTP status 200")
    failed_checks = []
    for assertion_id, check in target.checks.items():
        try:
            passed = check(body, final_url, content_type)
        except ProductValidationError:
            raise
        except Exception:
            raise
        aggregate[assertion_id] &= passed
        if not passed:
            failed_checks.append(assertion_id.replace("_", " "))
    if failed_checks:
        raise ProductValidationError(
            "response did not satisfy " + ", ".join(failed_checks)
        )


def _observation(target, samples, aggregate, error):
    failed = bool(error) or any(not value for value in aggregate.values())
    successful = tuple(
        sample.duration_seconds for sample in samples if sample.successful
    )
    return ScenarioObservation(
        scenario_id=target.scenario_id,
        target_id=target.target_id,
        name=target.name,
        functional_verdict=(
            FunctionalVerdict.FAILURE if failed else FunctionalVerdict.PASS
        ),
        assertions=tuple(
            AssertionResult(
                f"{target.target_id}.{assertion_id}",
                passed,
                None if passed else "required response validation failed",
            )
            for assertion_id, passed in aggregate.items()
        ),
        samples=samples,
        statistics=calculate_timing_statistics(successful) if successful else None,
        error_message=error,
    )


def _failed_observation(target, samples, aggregate, error):
    if all(aggregate.values()):
        aggregate = {"available": False}
    return _observation(target, samples, aggregate, error)


def _contains_marker(marker):
    return lambda body, _url, _content_type: marker in _decode(body)


def _json_list(body, _url, _content_type):
    return isinstance(_json_body(body), list)


def _is_json_content_type(_body, _url, content_type):
    return content_type == "application/json"


def _posts_contain_title(body, expected_title):
    document = _json_body(body)
    if not isinstance(document, list):
        return False
    return any(
        isinstance(post, dict)
        and isinstance(post.get("title"), dict)
        and post["title"].get("rendered") == expected_title
        for post in document
    )


def _json_body(body):
    return json.loads(_decode(body))


def _decode(body):
    return body.decode("utf-8")


def _validated_base_url(value):
    if not isinstance(value, str) or not value:
        raise ValueError("base URL must be non-empty")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must use HTTP or HTTPS and include a host")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a query or fragment")
    return value.rstrip("/") + "/"


def _target_url(base_url, path):
    relative_path = (
        path.lstrip("/")
        if urlsplit(base_url).path not in ("", "/")
        else path
    )
    return urljoin(base_url, relative_path)


def _build_product_opener(approved_urls):
    return build_opener(_SameOriginRedirectHandler(approved_urls))


def _response_content_type(response):
    value = response.headers.get("Content-Type")
    if not isinstance(value, str):
        return None
    return value.partition(";")[0].strip().lower() or None


def _origin(value, *, reject_credentials=False):
    parsed = urlsplit(value)
    if reject_credentials and (parsed.username or parsed.password):
        raise ValueError("URL credentials are not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _url_key(value, *, reject_credentials=False):
    parsed = urlsplit(value)
    origin = _origin(value, reject_credentials=reject_credentials)
    return (*origin, parsed.path or "/", parsed.query)


def _failure_duration(error):
    return getattr(error, "_openstack_perf_duration", 0.0)


def _require_positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be at least 1")


def _require_http_timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_seconds must be a number")
    if not math.isfinite(value) or value <= 0 or value > DEFAULT_TIMEOUT_SECONDS:
        raise ValueError(
            "timeout_seconds must be finite, positive, and at most 10 seconds"
        )
