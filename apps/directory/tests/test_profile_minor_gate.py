"""The under-18 gate on the public profile.

This is the test the Phase 3 gate is written around, and it is deliberately in a
file of its own so it cannot be lost in a large module.

A PROVISIONAL practitioner is live for **adult work only** while an enhanced DBS
is pending. Their under-18 client groups must be ABSENT FROM THE HTML — not
hidden with CSS, not folded into a `hidden` attribute, not sitting in a data
attribute for JavaScript to filter. Anything that reaches the browser reaches a
scraper, an answer engine and "view source", and the point of the gate is that a
parent searching for someone to see their child never sees a listing whose DBS
has not been checked.

The assertions are therefore against the raw response body, not against a parsed
context or a queryset. `assert name not in body` is the only assertion that
actually tests what the rule says.

The second half of the gate — `queryset.filter(minor_work_status=CLEARED)` in the
search view — is Phase 4 and does not exist yet. Either half alone leaks. See
CLAUDE.md.
"""

from __future__ import annotations

import pytest

from apps.directory.factories import ClientGroupFactory, PractitionerFactory
from apps.directory.models import MinorWorkStatus
from apps.directory.services import verification

pytestmark = pytest.mark.django_db

ADULTS = "Adults (18 and over)"
MINORS = "Adolescents (12 to 17)"


@pytest.fixture
def groups():
    return (
        ClientGroupFactory(slug="adults", name=ADULTS, min_age=18, is_minors=False),
        ClientGroupFactory(slug="adolescents", name=MINORS, min_age=12, max_age=17, is_minors=True),
    )


def _published(groups, **traits):
    adults, minors = groups
    return PractitionerFactory(
        published=True,
        full_name="Dr Provisional Example",
        slug="provisional-example",
        client_groups=[adults, minors],
        **traits,
    )


def _body(client, practitioner) -> str:
    response = client.get(f"/p/{practitioner.slug}/")
    assert response.status_code == 200
    return response.content.decode()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_a_provisional_practitioners_minor_groups_are_absent_from_the_html(client, groups):
    """THE Phase 3 gate. DBS pending means adult work only."""
    practitioner = _published(groups, provisional_dbs=True)
    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL

    body = _body(client, practitioner)

    # The listing renders, and it renders its adult groups.
    assert ADULTS in body

    # And the under-18 group is nowhere in the document. Not its name, not its
    # slug — nothing a parser could pick up.
    assert MINORS not in body
    assert "Adolescents" not in body
    assert "adolescents" not in body


def test_a_blocked_practitioners_minor_groups_are_absent_from_the_html(client, groups):
    """The window ran out, or the DBS was rejected. Same public effect."""
    practitioner = _published(groups)
    verification.recompute(practitioner)
    practitioner.refresh_from_db()
    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED

    body = _body(client, practitioner)

    assert ADULTS in body
    assert "Adolescents" not in body


def test_a_cleared_practitioners_minor_groups_do_render(client, groups):
    """The gate has to open as well as close, or it is just a bug."""
    practitioner = _published(groups, dbs_cleared=True)
    assert practitioner.minor_work_status == MinorWorkStatus.CLEARED

    body = _body(client, practitioner)

    assert ADULTS in body
    assert MINORS in body


# ---------------------------------------------------------------------------
# The gate is the service's, not the template's
# ---------------------------------------------------------------------------


def test_the_view_model_never_carries_the_hidden_groups(groups):
    """Absent from the CONTEXT, not merely unrendered.

    A group that reaches the template and is skipped by an `{% if %}` is one
    template edit away from being printed. The service filters it out before the
    template can see it.
    """
    from apps.directory.services import profile as profile_service

    practitioner = _published(groups, provisional_dbs=True)
    names = {group.name for group in profile_service.build(practitioner)["client_groups"]}

    assert names == {ADULTS}


def _code_only(path) -> str:
    """The file with its comments and docstrings removed.

    Both files below *talk about* `practitioner.client_groups` at length, saying
    not to use it. Grepping the raw text would match the warning and fail on a
    correct file — so the prose comes out first and only executable code is
    searched.
    """
    import io
    import re
    import tokenize

    source = path.read_text()

    if path.suffix == ".html":
        source = re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", "", source, flags=re.DOTALL)
        return re.sub(r"{#.*?#}", "", source, flags=re.DOTALL)

    return " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in (tokenize.COMMENT, tokenize.STRING)
    )


def test_the_profile_reads_client_groups_through_the_gate_only():
    """A grep, deliberately.

    `visible_client_groups()` is easy to bypass by accident — `client_groups` is
    right there on the model and reads identically. This asserts that neither the
    profile template nor its service ever touches the ungated relation, which is
    the check that survives somebody rewriting the template around the rendering
    tests.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    watched = [
        repo / "templates" / "directory" / "profile.html",
        repo / "apps" / "directory" / "services" / "profile.py",
    ]

    for path in watched:
        assert path.exists(), f"{path} has moved — this grep is watching nothing"
        code = _code_only(path)
        assert "client_groups.all" not in code, f"{path} reads client_groups without the gate"
        assert "practitioner.client_groups" not in code, f"{path} reads client_groups without the gate"
