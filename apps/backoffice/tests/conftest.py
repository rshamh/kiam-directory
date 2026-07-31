"""Fixtures for the back-office suite."""

import pytest

from apps.directory.factories import PractitionerFactory


@pytest.fixture
def practitioner_profile(practitioner):
    """A submittable draft owned by the `practitioner` user fixture.

    Deliberately lint-clean: tests that want a finding introduce it themselves,
    so a failure points at the rule under test rather than at the fixture.
    """
    return PractitionerFactory(
        user=practitioner,
        full_name="Dr Test Practitioner",
        intro="I support adults with anxiety and low mood.",
        services="Assessment and weekly therapy sessions.",
        public_email="listed@example.com",
    )


@pytest.fixture
def document():
    """An evidence file on the private backend."""
    from django.core.files.base import ContentFile

    from apps.directory.models import Document, VerificationType

    profile = PractitionerFactory()
    doc = Document(
        practitioner=profile,
        type=VerificationType.DBS,
        original_filename="dbs.pdf",
        mime_type="application/pdf",
        size_bytes=42,
        sha256="0" * 64,
    )
    doc.file.save("dbs.pdf", ContentFile(b"%PDF evidence"), save=True)
    return doc
