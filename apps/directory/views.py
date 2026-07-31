"""Public practitioner pages. Thin — the work is in ``directory/services/``.

Two views and one rule between them: nothing that is not PUBLISHED renders, and
everything that does not render fails the same way. A draft, a suspension, an
unpublished listing and a slug nobody has ever used all produce the same 404
page with the same status code. See ``directory/services/profile.py``.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.services import ratelimit
from apps.seo import jsonld

from .services import metrics
from .services import profile as profile_service

logger = logging.getLogger("directory.profile")


@require_GET
def profile(request, slug: str):
    resolution = profile_service.resolve(slug)

    if resolution.redirect_to:
        # 301, not 302: the old slug is gone for good and the accrued authority
        # should move with it (docs/seo.md).
        return redirect(resolution.redirect_to, permanent=True)

    practitioner = resolution.practitioner
    if practitioner is None:
        raise Http404("No listing at this address.")

    metrics.record_profile_view(practitioner, request=request)

    context = profile_service.build(practitioner)
    context.update(_seo(request, practitioner, context))
    return render(request, "directory/profile.html", context)


@require_POST
def contact_reveal(request, slug: str, channel: str):
    """Reveal one contact detail, and count it.

    POST-only, and that is the bot protection that actually matters: the address
    is not in the page until someone asks for it, and asking requires a CSRF
    token from a rendered form. A scraper pulling the profile HTML gets nothing,
    and one that follows links gets nothing either, because there is no URL here
    a crawler can reach with a GET.

    On top of that, a per-IP rate limit, because "not in the HTML" is only a
    speed bump against something that is willing to POST.

    There is no enquiry form and no message relay, deliberately. Kiam passes on
    no messages; relaying them would make Kiam a processor of the client's
    health-adjacent personal data and change its data-controller position, which
    needs legal review before anyone builds it (docs/content-compliance.md §9).
    """
    resolution = profile_service.resolve(slug)
    practitioner = resolution.practitioner
    if practitioner is None:
        # Includes the redirect case: an old slug is for reading a profile, not
        # for posting to.
        raise Http404("No listing at this address.")

    spec = profile_service.CHANNELS.get(channel)
    value = profile_service.channel_value(practitioner, channel)
    if spec is None or not value:
        raise Http404("No such contact channel on this listing.")

    context = {
        "practitioner": practitioner,
        "display_name": profile_service.display_name(practitioner),
        "profile_url": profile_service.profile_path(practitioner),
        "channel": spec,
        # h3 inside the profile's "Contact" section; the standalone page
        # overrides it to h2. Heading level is a parameter, never a style
        # (docs/design-system.md §4).
        "heading_level": 3,
    }

    if _rate_limited(request):
        logger.warning("profile.reveal_rate_limited", extra={"slug": slug, "channel": channel})
        return _reveal_response(request, "directory/_contact_limited.html", context, status=429)

    metrics.record_reveal(practitioner, channel)

    return _reveal_response(
        request,
        "directory/_contact_revealed.html",
        {
            **context,
            "value": value,
            "href": profile_service.channel_href(channel, value),
        },
    )


def _rate_limited(request) -> bool:
    ip = ratelimit.client_ip(request)
    if not ip:
        return False
    result = ratelimit.hit(
        "contact_reveal",
        ip,
        limit=settings.CONTACT_REVEAL_MAX_PER_IP,
        window_seconds=settings.CONTACT_REVEAL_WINDOW_SECONDS,
    )
    return result.exceeded


def _reveal_response(request, partial: str, context: dict, *, status: int = 200):
    """The partial for HTMX, a whole page for everyone else.

    The no-JavaScript path is not a courtesy. A plain form post has to work, or
    the contact details are unreachable for anyone whose JavaScript failed to
    load — and this audience includes people on locked-down or assistive setups
    where that is routine.
    """
    if request.htmx:
        response = render(request, partial, context, status=status)
    else:
        response = render(
            request,
            "directory/contact_reveal.html",
            {
                **context,
                "panel_template": partial,
                "heading_level": 2,
                "meta_title": f"Contact {context['display_name']}",
                "meta_description": (
                    f"Contact details for {context['display_name']}, an independent "
                    "practitioner listed in the Kiam Clinic Directory."
                ),
            },
            status=status,
        )
    # Belt and braces: the page is POST-only, so a crawler cannot reach it, but
    # nothing is lost by saying so.
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _seo(request, practitioner, context: dict) -> dict:
    """Title, description, breadcrumbs and the ``Person`` + ``ProfilePage`` graph."""
    path = profile_service.profile_path(practitioner)
    name = context["display_name"]
    profession = practitioner.profession.name if practitioner.profession else ""

    # `build_absolute_uri` rather than string concatenation, and the result is
    # shared with the template's og:image rather than built twice.
    #
    # Under FileSystemStorage `headshot.url` is "/media/x.jpg" and prefixing the
    # host is right. Under the S3 backend production uses when PUBLIC_MEDIA_BUCKET
    # is set (config/settings/prod.py) it is ALREADY absolute, and prefixing gives
    # "https://directory.kiamclinic.comhttps://bucket.s3…". build_absolute_uri
    # returns an absolute URL untouched, so it is correct under both.
    image_url = ""
    if practitioner.headshot:
        image_url = request.build_absolute_uri(practitioner.headshot.url)

    work_locations = [
        jsonld.place(
            name=entry["location"].label,
            street=entry["location"].address_line1,
            locality=entry["location"].city,
            region=entry["location"].county,
            postcode=entry["location"].postcode,
        )
        for entry in context["locations"]
    ]

    person_node = jsonld.person(
        path=path,
        name=name,
        honorific_prefix=practitioner.display_title,
        honorific_suffix=practitioner.post_nominals,
        job_title=profession,
        description=context["answer_sentence"],
        image_url=image_url,
        languages=[language.name for language in context["languages"]],
        knows_about=(
            [speciality.name for speciality in context["specialities"]]
            + [approach.name for approach in context["approaches"]]
        ),
        work_locations=work_locations,
    )

    title = f"{name} — {profession}" if profession else name
    description = profile_service.meta_description(practitioner)

    return {
        "meta_title": title,
        "meta_description": description,
        "og_image_url": image_url,
        "breadcrumbs": jsonld.breadcrumb_items(("Home", "/"), (name, path)),
        "jsonld": jsonld.practitioner_profile(
            person_node=person_node,
            page_node=jsonld.profile_page(
                path=path,
                name=title,
                description=description,
                modified=practitioner.updated_at,
            ),
        ),
    }
