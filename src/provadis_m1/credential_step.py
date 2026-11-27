"""One bounded credential transaction, with conservative click recovery.

Only matching POST request metadata proves submission. No body, header, input
value, or form-action URL enters diagnostics. DOM evidence is document-local.
"""

import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .errors import AuthenticationError, BootstrapError, M1Error

OUTCOMES = {"not_started", "prepared", "awaiting_request", "uncertain", "request_observed"}

# Install before Microsoft scripts to record activation/submit dispatch before
# application handlers run. This does not alter the events.
CREDENTIAL_CAPTURE_SCRIPT = r"""
(() => {
  if (location.origin !== 'https://login.microsoftonline.com') return;
  if (window.__otterCredentialEvidence) return;
  const evidence = {records: Object.create(null), observerInstalled: true};
  window.__otterCredentialEvidence = evidence;
  const observe = event => {
    for (const record of Object.values(evidence.records)) {
      const path = event.composedPath();
      const matches = form => form && (form === record.form || form.action === record.action);
      if ((event.type === 'submit' && matches(event.target)) ||
          (['pointerdown', 'mousedown', 'click'].includes(event.type) && path.some(node =>
            node && node.id === 'idSIButton9' && matches(node.form)))) {
        record.dispatched = true;
      }
    }
  };
  window.addEventListener('pointerdown', observe, true);
  window.addEventListener('mousedown', observe, true);
  window.addEventListener('click', observe, true);
  window.addEventListener('submit', observe, true);
})();
"""

PREPARE = r"""(node, expected) => {
  const doc = node.ownerDocument;
  if (doc.location.origin !== expected.host) return {status: 'origin_changed'};
  if (!node.isConnected) return {status: 'detached'};
  const visible = n => n.isConnected && n.getClientRects().length > 0 &&
    getComputedStyle(n).visibility !== 'hidden';
  const fields = Array.from(doc.querySelectorAll(expected.marker)).filter(visible);
  if (fields.length !== 1 || fields[0] !== node) return {status: 'form_changed'};
  const form = node.form;
  if (!form || form.method.toLowerCase() !== 'post') return {status: 'form_changed'};
  const evidence = window.__otterCredentialEvidence;
  if (!evidence || !evidence.observerInstalled) return {status: 'observer_missing'};
  let record = evidence.records[expected.token];
  if (!record && !expected.initial) return {status: 'evidence_lost'};
  if (record && record.dispatched) return {status: 'dispatched'};
  if (record && record.action !== form.action) return {status: 'form_changed'};
  if (!record) record = evidence.records[expected.token] = {dispatched: false};
  record.form = form;
  record.action = form.action;
  return {status: 'ready', action: form.action};
}"""

VERIFY_SUBMIT = r"""(node, expected) => {
  const doc = node.ownerDocument;
  if (doc.location.origin !== expected.host) return {status: 'origin_changed'};
  if (!node.isConnected) return {status: 'detached'};
  const record = window.__otterCredentialEvidence?.records[expected.token];
  if (!record) return {status: 'evidence_lost'};
  if (record.dispatched) return {status: 'dispatched'};
  const fields = Array.from(doc.querySelectorAll(expected.marker)).filter(n =>
    n.isConnected && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== 'hidden');
  if (fields.length !== 1 || node.form !== fields[0].form ||
      node.form !== record.form || node.form.action !== record.action)
    return {status: 'form_changed'};
  return {status: 'ready'};
}"""

READ_EVIDENCE = r"""token => {
  const record = window.__otterCredentialEvidence?.records[token];
  return record ? {dispatched: record.dispatched} : null;
}"""


class ControlChanged(Exception):
    pass


@dataclass
class CredentialStep:
    page: Any = field(repr=False)
    phase: str
    marker: str
    host: str
    operation: Any = field(repr=False)
    on_error: Any = field(repr=False)
    trace: Any = field(default=None, repr=False)
    token: str = field(default_factory=lambda: uuid.uuid4().hex, repr=False)
    action: str | None = field(default=None, repr=False)
    outcome: str = "not_started"
    recoveries: int = 0
    observed: bool = False
    progressed: bool = False
    active: bool = True

    def observe_request(self, request: Any) -> None:
        try:
            matches = (self.active and self.action is not None and request.method == "POST"
                       and request.url == self.action and request.frame == self.page.main_frame)
        except Exception:
            return  # Inaccessible frames yield no positive evidence.
        if matches and not self.observed:
            self.observed, self.outcome = True, "request_observed"
            if self.trace:
                self.trace("credential_request_observed", state=self.phase)

    def _origin(self) -> None:
        url = urlsplit(self.page.url)
        if f"{url.scheme}://{url.netloc}" != self.host:
            raise AuthenticationError("authentication_control_origin_changed")

    def _record(self) -> dict | None:
        try:
            self._origin()
            record = self.page.evaluate(READ_EVIDENCE, self.token)
            return record if isinstance(record, dict) and type(record.get("dispatched")) is bool else None
        except Exception:
            return None

    def _status(self, data: dict) -> bool:
        status = data.get("status")
        if status == "origin_changed":
            raise AuthenticationError("authentication_control_origin_changed")
        if status == "observer_missing":
            raise BootstrapError("credential_observer_not_installed")
        if status in {"evidence_lost", "dispatched"}:
            self.outcome = "uncertain"
            return False
        if status in {"detached", "form_changed"}:
            raise ControlChanged()
        if status != "ready":
            raise BootstrapError("credential_control_verification_failed")
        return True

    def run(self, value: str, timeout_ms: int) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        initial = True

        def remaining() -> int:
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0:
                raise BootstrapError("credential_control_recovery_timeout")
            return left

        expected = {"host": self.host, "marker": self.marker, "token": self.token}
        while True:
            handles = []
            try:
                remaining()
                self._origin()
                self.operation("resolve_control")
                field = self.page.locator(self.marker).filter(visible=True)
                if field.count() > 1:
                    raise BootstrapError("ambiguous_credential_field")
                field = field.element_handle(timeout=remaining())
                if field is None:
                    raise ControlChanged()
                handles.append(field)
                self.operation("verify_document")
                data = field.evaluate(PREPARE, {**expected, "initial": initial})
                if not self._status(data):
                    return
                action = data.get("action")
                parsed = urlsplit(action) if isinstance(action, str) else None
                if parsed is None or f"{parsed.scheme}://{parsed.netloc}" != self.host:
                    raise AuthenticationError("credential_form_action_untrusted")
                self.action, self.outcome, initial = action, "prepared", False
                self.operation("fill_control")
                field.fill(value, timeout=remaining())
                if self.observed:
                    return
                self._origin()
                self.operation("resolve_control")
                submit = self.page.locator("#idSIButton9").filter(visible=True)
                if submit.count() > 1:
                    raise BootstrapError("ambiguous_credential_submit")
                submit = submit.element_handle(timeout=remaining())
                if submit is None:
                    raise ControlChanged()
                handles.append(submit)
                self.operation("verify_document")
                if not self._status(submit.evaluate(VERIFY_SUBMIT, expected)):
                    return
                if self.observed:
                    return
                self.operation("click_control")
                submit.click(timeout=remaining())
                if not self.observed:
                    self.outcome = "awaiting_request"
                return
            except M1Error:
                raise
            except Exception as error:
                if self.observed:
                    return
                record = self._record()
                message = str(error).lower()
                detached = isinstance(error, ControlChanged) or any(text in message for text in (
                    "not attached to the dom", "element is detached"))
                if (record is None and not initial) or (record is not None and record["dispatched"]):
                    # Activation/submit dispatch or document loss makes the outcome
                    # uncertain. Await state/network evidence; do not click twice.
                    self.outcome = "uncertain"
                    self.on_error(error)
                    if self.trace:
                        self.trace("credential_submission_uncertain", state=self.phase)
                    return
                if not detached:
                    raise
                self.on_error(error)
                if self.recoveries >= 3:
                    raise BootstrapError("credential_control_recovery_exhausted") from None
                remaining()
                self.recoveries += 1
                # The next iteration rechecks the current page origin, active
                # field/form, and document-local no-dispatch record before fill.
                if self.trace:
                    self.trace("credential_control_reacquired", state=self.phase)
            finally:
                for handle in handles:
                    with suppress(Exception):
                        handle.dispose()
