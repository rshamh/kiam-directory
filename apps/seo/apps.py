from django.apps import AppConfig


class SeoConfig(AppConfig):
    """robots.txt, sitemap.xml, llms.txt and the JSON-LD helpers. This subdomain owns all of them; none is shared."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.seo"
    verbose_name = "SEO"
