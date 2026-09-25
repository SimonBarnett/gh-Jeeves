# G1 TLS fixtures (FR #72)

Do **not** commit private keys or PEMs here. GitGuardian and repo policy forbid
checked-in RSA private keys.

`jeeves.tls_irc.make_self_signed_cert` generates ephemeral certs via the
`cryptography` package (dev dependency). openssl is optional fallback only.
