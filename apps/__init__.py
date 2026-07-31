"""Every Django app in this project.

A container package, not an app: it ships no models, no migrations and no
AppConfig, and `INSTALLED_APPS` names its children rather than it.

The app LABELS are unchanged by living here. Django derives a label from the last
component of the dotted path, so `apps.accounts` is still `accounts` — which is
what `AUTH_USER_MODEL = "accounts.User"`, every migration dependency, every
`ContentType` row and every `apps.get_model("directory", ...)` call resolves
against. Moving the directories therefore needed no migration and no data change.

**Prose in this repo names modules app-relative.** A docstring saying
``directory.services.verification`` means the file
``apps/directory/services/verification.py``; the import is
``apps.directory.services.verification``. Rewriting 374 comment references would
have buried the layout change in churn, and the app-relative name is the one that
matches the app label. Import statements, settings paths and anything a developer
would paste into a shell are fully qualified.
"""
