"""Opt-in simulation of declining WebAuthn, never an authentication assertion."""

DECLINE_PASSKEY_SCRIPT = r"""
(() => {
  const allowed = location.origin === 'https://login.microsoftonline.com' ||
    (location.origin === 'https://login.microsoft.com' && location.pathname === '/common/bridge/fido');
  if (!allowed || !navigator.credentials) return;
  const original = navigator.credentials.get.bind(navigator.credentials);
  navigator.credentials.get = function(options) {
    if (options && options.publicKey) {
      return Promise.reject(new DOMException('Passkey declined by operator configuration', 'NotAllowedError'));
    }
    return original(options);
  };
})();
"""
