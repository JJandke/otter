"""Opt-in simulation of declining WebAuthn, never an authentication assertion."""

DECLINE_PASSKEY_SCRIPT = r"""
(() => {
  if (location.origin !== 'https://login.microsoftonline.com' || !navigator.credentials) return;
  const original = navigator.credentials.get.bind(navigator.credentials);
  navigator.credentials.get = function(options) {
    if (options && options.publicKey) {
      return Promise.reject(new DOMException('Passkey declined by operator configuration', 'NotAllowedError'));
    }
    return original(options);
  };
})();
"""
