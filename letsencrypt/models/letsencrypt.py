# © 2016 Therp BV <http://therp.nl>
# © 2016 Antonio Espinosa <antonio.espinosa@tecnativa.com>
# © 2018 Ignacio Ibeas <ignacio@acysos.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import base64
import collections
import logging
import os
import re
import subprocess
import time
import urllib.parse

from datetime import datetime, timedelta

import requests

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import config

_logger = logging.getLogger(__name__)
try:
    import acme.challenges
    import acme.client
    import acme.crypto_util
    import acme.errors
    import acme.messages

    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    import dns.resolver

    import josepy
except ImportError as e:
    _logger.debug(e)

WILDCARD = '*.'  # as defined in the spec
DEFAULT_KEY_LENGTH = 4096
TYPE_CHALLENGE_HTTP = 'http-01'
TYPE_CHALLENGE_DNS = 'dns-01'
V2_STAGING_DIRECTORY_URL = (
    'https://acme-staging-v02.api.letsencrypt.org/directory'
)
V2_DIRECTORY_URL = 'https://acme-v02.api.letsencrypt.org/directory'
LOCAL_DOMAINS = {
    'localhost',
    'localhost.localdomain',
    'localhost6',
    'localhost6.localdomain6',
    'ip6-localhost',
    'ip6-loopback',
}

DNSUpdate = collections.namedtuple(
    "DNSUpdate", ("challenge", "domain", "token")
)


def _get_data_dir():
    dir_ = os.path.join(config.options.get('data_dir'), 'letsencrypt')
    if not os.path.isdir(dir_):
        os.makedirs(dir_)
    return dir_


def _get_challenge_dir():
    dir_ = os.path.join(_get_data_dir(), 'acme-challenge')
    if not os.path.isdir(dir_):
        os.makedirs(dir_)
    return dir_


class Letsencrypt(models.AbstractModel):
    _name = 'letsencrypt'
    _description = 'Abstract model providing functions for letsencrypt'

    @api.model
    def _generate_key(self):
        """Generate an entirely new key."""
        return rsa.generate_private_key(
            public_exponent=65537,
            key_size=DEFAULT_KEY_LENGTH,
            backend=default_backend(),
        ).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @api.model
    def _get_key(self, key_name):
        """Get a key for a filename, generating if if it doesn't exist."""
        key_file = os.path.join(_get_data_dir(), key_name)
        if not os.path.isfile(key_file):
            _logger.info("Generating new key %s", key_name)
            key_bytes = self._generate_key()
            try:
                with open(key_file, 'wb') as file_:
                    os.fchmod(file_.fileno(), 0o600)
                    file_.write(key_bytes)
            except BaseException:
                # An incomplete file would block generation of a new one
                if os.path.isfile(key_file):
                    os.remove(key_file)
                raise
        else:
            _logger.info("Getting existing key %s", key_name)
            with open(key_file, 'rb') as file_:
                key_bytes = file_.read()
        return key_bytes

    @api.model
    def _validate_domain(self, domain):
        """Validate that a domain is publicly accessible."""
        if ':' in domain or all(
            char.isdigit() or char == '.' for char in domain
        ):
            raise UserError(
                _("Domain %s: Let's Encrypt doesn't support IP addresses!")
                % domain
            )

        if domain in LOCAL_DOMAINS or '.' not in domain:
            raise UserError(
                _("Domain %s: Let's encrypt doesn't work with local domains!")
                % domain
            )

    @api.model
    def _should_run(self, cert_file, domains):
        """Inspect the existing certificate to see if action is necessary."""
        domains = set(domains)

        if not os.path.isfile(cert_file):
            _logger.info("No existing certificate found, creating a new one")
            return True

        with open(cert_file, 'rb') as file_:
            cert = x509.load_pem_x509_certificate(
                file_.read(), default_backend()
            )
        expiry = cert.not_valid_after
        remaining = expiry - datetime.now()
        if remaining < timedelta():
            _logger.warning(
                "Certificate expired on %s, which was %d days ago!",
                expiry,
                -remaining.days,
            )
            _logger.info("Renewing certificate now.")
            return True
        if remaining < timedelta(days=30):
            _logger.info(
                "Certificate expires on %s, which is in %d days, renewing it",
                expiry,
                remaining.days,
            )
            return True

        # Should be a single name, but this is how the API works
        names = {
            entry.value
            for entry in cert.subject.get_attributes_for_oid(
                x509.oid.NameOID.COMMON_NAME
            )
        }
        try:
            names.update(
                cert.extensions.get_extension_for_oid(
                    x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME
                ).value.get_values_for_type(x509.DNSName)
            )
        except x509.extensions.ExtensionNotFound:
            pass

        missing = domains - names
        if missing:
            _logger.info(
                "Found new domains %s, requesting new certificate",
                ', '.join(missing),
            )
            return True

        _logger.info(
            "Certificate expires on %s, which is in %d days, no action needed",
            expiry,
            remaining.days,
        )
        return False

    @api.model
    def _cron(self):
        ir_config_parameter = self.env['ir.config_parameter']
        base_url = ir_config_parameter.get_param('web.base.url', 'localhost')
        domain = urllib.parse.urlparse(base_url).hostname
        cert_file = os.path.join(_get_data_dir(), '%s.crt' % domain)

        domains = self._cascade_domains([domain] + self._get_altnames())
        for dom in domains:
            self._validate_domain(dom)

        if not self._should_run(cert_file, domains):
            return

        account_key = josepy.JWKRSA.load(self._get_key('account.key'))
        domain_key = self._get_key('%s.key' % domain)

        client = self._create_client(account_key)
        new_reg = acme.messages.NewRegistration(
            key=account_key.public_key(), terms_of_service_agreed=True
        )
        try:
            client.new_account(new_reg)
            _logger.info("Successfully registered.")
        except acme.errors.ConflictError as err:
            reg = acme.messages.Registration(key=account_key.public_key())
            reg_res = acme.messages.RegistrationResource(
                body=reg, uri=err.location
            )
            client.query_registration(reg_res)
            _logger.info("Reusing existing account.")

        _logger.info('Making CSR for the following domains: %s', domains)
        csr = acme.crypto_util.make_csr(
            private_key_pem=domain_key, domains=domains
        )
        authzr = client.new_order(csr)

        # For each requested domain name we receive a list of challenges.
        # We only have to do one from each list.
        # HTTP challenges are the easiest, so do one of those if possible.
        # We can do DNS challenges too. There are other types that we don't
        # support.
        pending_responses = []

        prefer_dns = (
            self.env["ir.config_parameter"].get_param("letsencrypt.prefer_dns")
            == "True"
        )
        for authorizations in authzr.authorizations:
            http_challenges = [
                challenge
                for challenge in authorizations.body.challenges
                if challenge.chall.typ == TYPE_CHALLENGE_HTTP
            ]
            other_challenges = [
                challenge
                for challenge in authorizations.body.challenges
                if challenge.chall.typ != TYPE_CHALLENGE_HTTP
            ]
            if prefer_dns:
                ordered_challenges = other_challenges + http_challenges
            else:
                ordered_challenges = http_challenges + other_challenges
            for challenge in ordered_challenges:
                if challenge.chall.typ == TYPE_CHALLENGE_HTTP:
                    self._respond_challenge_http(challenge, account_key)
                    client.answer_challenge(
                        challenge, acme.challenges.HTTP01Response()
                    )
                    break
                elif challenge.chall.typ == TYPE_CHALLENGE_DNS:
                    domain = authorizations.body.identifier.value
                    token = challenge.validation(account_key)
                    self._respond_challenge_dns(domain, token)
                    # We delay this because we wait for each domain.
                    # That takes less time if they've all already been changed.
                    pending_responses.append(
                        DNSUpdate(
                            challenge=challenge, domain=domain, token=token
                        )
                    )
                    break
            else:
                raise UserError(
                    _('Could not respond to letsencrypt challenges.')
                )

        if pending_responses:
            for update in pending_responses:
                self._wait_for_record(update.domain, update.token)
            # 1 minute was not always enough during testing, even once records
            # were visible locally
            _logger.info(
                "All TXT records found, waiting 5 minutes more to make sure."
            )
            time.sleep(300)
            for update in pending_responses:
                client.answer_challenge(
                    update.challenge, acme.challenges.DNSResponse()
                )

        # let them know we are done and they should check
        backoff = int(ir_config_parameter.get_param('letsencrypt.backoff', 3))
        deadline = datetime.now() + timedelta(minutes=backoff)
        try:
            order_resource = client.poll_and_finalize(authzr, deadline)
        except acme.errors.ValidationError as error:
            _logger.error("Let's Encrypt validation failed!")
            for authz in error.failed_authzrs:
                for challenge in authz.body.challenges:
                    _logger.error(str(challenge.error))
            raise

        with open(cert_file, 'w') as crt:
            crt.write(order_resource.fullchain_pem)
        _logger.info('SUCCESS: Certificate saved: %s', cert_file)
        reload_cmd = ir_config_parameter.get_param(
            'letsencrypt.reload_command', ''
        )
        if reload_cmd.strip():
            self._call_cmdline(reload_cmd)
        else:
            _logger.warning("No reload command defined.")

    @api.model
    def _wait_for_record(self, domain, token):
        """Wait until a TXT record for a domain is visible."""
        if not domain.endswith("."):
            # Fully qualify domain name, or it may try unsuitable names too
            domain += "."
        attempt = 0
        while True:
            attempt += 1
            try:
                for record in dns.resolver.query(
                    "_acme-challenge." + domain, "TXT"
                ):
                    value = record.to_text()[1:-1]
                    if value == token:
                        return
                    else:
                        _logger.debug("Found %r instead of %r", value, token)
            except dns.resolver.NXDOMAIN:
                _logger.debug("Record for %r does not exist yet", domain)
            if attempt < 30:
                _logger.info("Waiting for DNS update.")
                time.sleep(60)
            else:
                _logger.warning(
                    "Could not find new record after 30 minutes! "
                    "Giving up and hoping for the best."
                )
                return

    @api.model
    def _create_client(self, account_key):
        param = self.env['ir.config_parameter']
        testing_mode = param.get_param('letsencrypt.testing_mode') == 'True'
        if config['test_enable'] or testing_mode:
            directory_url = V2_STAGING_DIRECTORY_URL
        else:
            directory_url = V2_DIRECTORY_URL
        directory_json = requests.get(directory_url).json()
        net = acme.client.ClientNetwork(account_key)
        return acme.client.ClientV2(directory_json, net)

    @api.model
    def _cascade_domains(self, domains):
        """Remove domains that are obsoleted by wildcard domains in the list.

        Requesting www.example.com is unnecessary if *.example.com is also
        requested. example.com isn't obsoleted however, and neither is
        sub.domain.example.com.
        """
        to_remove = set()
        for domain in domains:
            if WILDCARD in domain[1:]:
                raise UserError(
                    _("A wildcard is only allowed at the start of a domain")
                )
            if domain.startswith(WILDCARD):
                postfix = domain[1:]  # e.g. ".example.com"
                # This makes it O(n²) but n <= 100 so it's ok
                for other in domains:
                    if other.startswith(WILDCARD):
                        continue
                    if other.endswith(postfix):
                        prefix = other[: -len(postfix)]  # e.g. "www"
                        if '.' not in prefix:
                            to_remove.add(other)

        return sorted(set(domains) - to_remove)

    @api.model
    def _get_altnames(self):
        """Get the configured altnames as a list of strings."""
        altnames = self.env['ir.config_parameter'].get_param(
            'letsencrypt.altnames'
        )
        if not altnames:
            return []
        return re.split('(?:,|\n| |;)+', altnames)

    @api.model
    def _respond_challenge_http(self, challenge, account_key):
        """
        Respond to the HTTP challenge by writing the file to serve.
        """
        token = self._base64_encode(challenge.token)
        challenge_file = os.path.join(_get_challenge_dir(), token)
        with open(challenge_file, 'w') as file_:
            file_.write(challenge.validation(account_key))

    @api.model
    def _respond_challenge_dns(self, domain, token):
        """
        Respond to the DNS challenge by creating the DNS record
        on the provider.
        """
        provider = self.env['ir.config_parameter'].get_param(
            'letsencrypt.dns_provider'
        )
        if not provider:
            raise UserError(
                _("No DNS provider set, can't request wildcard certificate")
            )
        dns_function = getattr(self, "_respond_challenge_dns_" + provider)
        dns_function(domain.replace("*.", ""), token)

    @api.model
    def _call_cmdline(self, cmdline, env=None):
        """Call a shell command."""
        process = subprocess.Popen(
            cmdline,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            shell=True,
        )
        stdout, stderr = process.communicate()
        stdout = stdout.strip()
        stderr = stderr.strip()
        if process.returncode:
            if stdout:
                _logger.warning(stdout)
            if stderr:
                _logger.warning(stderr)
            raise UserError(
                _('Error calling %s: %d') % (cmdline, process.returncode)
            )
        if stdout:
            _logger.info(stdout)
        if stderr:
            _logger.info(stderr)

    @api.model
    def _respond_challenge_dns_shell(self, domain, token):
        """Respond to a DNS challenge using an arbitrary shell command."""
        script_str = self.env['ir.config_parameter'].get_param(
            'letsencrypt.dns_shell_script'
        )
        if script_str:
            env = os.environ.copy()
            env.update(
                LETSENCRYPT_DNS_DOMAIN=domain,
                LETSENCRYPT_DNS_CHALLENGE=token,
            )
            self._call_cmdline(script_str, env=env)
        else:
            raise UserError(
                _("No shell command configured for updating DNS records")
            )

    @api.model
    def _base64_encode(self, data):
        """Encode data as a URL-safe base64 string without padding.

        This should be the encoding that Let's Encrypt uses for all base64. See
        https://github.com/ietf-wg-acme/acme/issues/64#issuecomment-168852757
        and https://golang.org/pkg/encoding/base64/#RawURLEncoding
        """
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')
