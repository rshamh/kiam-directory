"""Public practitioner URLs.

``/p/<slug>/`` rather than ``/practitioner/<slug>/``: short, stable, and short
enough to survive being read aloud or printed on a card. It is also the prefix
the slug-redirect table is written against, so it does not move.

The reveal endpoint hangs off the profile rather than living at the root, so a
reveal always names the listing it belongs to — the metric it increments has to
be attributable to one practitioner, and a URL that carries the slug cannot be
mis-attributed by a bug elsewhere.
"""

from django.urls import path, register_converter

from . import views


class ContactChannelConverter:
    """Only the three channels that exist. Anything else never reaches the view."""

    regex = "email|phone|website"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(ContactChannelConverter, "channel")

app_name = "directory"

urlpatterns = [
    path("p/<slug:slug>/", views.profile, name="profile"),
    path("p/<slug:slug>/contact/<channel:channel>/", views.contact_reveal, name="contact_reveal"),
]
