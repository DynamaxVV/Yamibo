from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass

from yamibo_mcp.errors import is_direct_transport_fallback_error
from yamibo_mcp.yamibo.account_pool import AccountIdentity, borrow_yamibo_client
from yamibo_mcp.yamibo.client import FetchResult, daily_checkin_already_done, parse_daily_checkin_profile
from yamibo_mcp.yamibo.proxy_pool import ProxyBinding, activate_proxy_binding, select_random_proxy


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailySignInResult:
    identity: AccountIdentity
    result: FetchResult
    profile: dict[str, object] | None
    proxy_binding: ProxyBinding | None
    transport: str
    direct_fallback_error: str | None = None


def run_daily_sign_in(
    settings,
    *,
    account_id: str,
    check_only: bool = False,
) -> DailySignInResult:
    """Run a sign-in operation over local direct first, then a proxy.

    Direct mode disables both the configured system proxy and the Mihomo
    proxy URL. A proxy is selected only after a transport/anti-bot failure;
    authentication and permission responses are returned immediately.
    """
    proxy_binding: ProxyBinding | None = None
    fallback_error: str | None = None
    direct = True

    while True:
        try:
            with ExitStack() as stack:
                stack.enter_context(activate_proxy_binding(settings, proxy_binding))
                identity, client = stack.enter_context(
                    borrow_yamibo_client(
                        settings,
                        account_id=account_id,
                        proxy_url=proxy_binding.proxy_url if proxy_binding else None,
                        force_direct=direct,
                    )
                )
                if check_only:
                    result = client.fetch_daily_checkin_page()
                    profile = parse_daily_checkin_profile(result.html)
                    if profile is None:
                        profile = {
                            "today_status": "checked"
                            if daily_checkin_already_done(result.html)
                            else "not_checked"
                        }
                else:
                    result = client.sign_daily_checkin()
                    profile = parse_daily_checkin_profile(getattr(result, "html", ""))
                    if profile is None:
                        try:
                            profile = parse_daily_checkin_profile(client.fetch_daily_checkin_page().html)
                        except Exception:  # noqa: BLE001 - sign-in already succeeded
                            LOG.warning(
                                "Failed to refresh sign-in profile after success for account_id=%s",
                                identity.account_id,
                                exc_info=True,
                            )
                return DailySignInResult(
                    identity=identity,
                    result=result,
                    profile=profile,
                    proxy_binding=proxy_binding,
                    transport="direct" if direct else "proxy",
                    direct_fallback_error=fallback_error,
                )
        except Exception as exc:  # noqa: BLE001 - classify before selecting fallback
            if not direct or not is_direct_transport_fallback_error(exc):
                raise
            try:
                proxy_binding = select_random_proxy(settings)
            except Exception:
                LOG.warning("Failed to select proxy after direct sign-in failure", exc_info=True)
                raise exc from None
            if proxy_binding is None:
                raise
            fallback_error = str(exc)
            direct = False
            LOG.info(
                "Retrying daily sign-in through proxy after direct transport failure account_id=%s node=%s",
                account_id,
                proxy_binding.node,
            )
