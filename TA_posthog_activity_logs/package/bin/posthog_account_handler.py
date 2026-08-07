"""Account endpoint that refuses to carry a stored API key to a new host.

The host is operator-entered, so trusting the value itself is reasonable. Inheriting a key
across a change of destination is not: editing only the host on an existing account would
otherwise send a key its holder never re-entered somewhere new, which turns write access to
this app's configuration into a way to use a credential without ever seeing it. Splunk masks a
stored key on edit, so a submission that moves the host while leaving the key masked is
exactly that case.

The check refuses when it cannot read the current host. A host change it cannot reason about
is the case it exists to catch.
"""

from splunktaucclib.rest_handler.admin_external import AdminExternalHandler
from splunktaucclib.rest_handler.error import RestError

MASKED = "******"
RE_ENTER = "Re-enter the personal API key when you change the host."


class PostHogAccountHandler(AdminExternalHandler):
    def _current_host(self):
        entries = self.handler.get(self.callerArgs.id)
        for _, content, _ in entries:
            return (content or {}).get("posthog_host")
        return None

    def _reject_inherited_key(self):
        submitted_host = self.payload.get("posthog_host")
        if not submitted_host:
            return  # Host is not being changed, so no key can travel with it.

        submitted_key = self.payload.get("api_key")
        if submitted_key and submitted_key != MASKED:
            return  # A key was supplied, so the new host is explicitly authorized.

        try:
            current_host = self._current_host()
        except Exception:
            raise RestError(400, RE_ENTER)

        if current_host is not None and current_host != submitted_host:
            raise RestError(400, RE_ENTER)

    def handleEdit(self, confInfo):
        self._reject_inherited_key()
        AdminExternalHandler.handleEdit(self, confInfo)
