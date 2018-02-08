# -*- coding: utf-8 -*-
# Copyright 2018 Therp BV <http://therp.nl>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from odoo.tests import SingleTransactionCase
from acme import errors
from os import path
import mock
import urlparse
import logging

_logger = logging.getLogger(__name__)

try:
    import josepy as jose
except ImportError as e:
    _logger.debug(e)


class TestLetsencrypt(SingleTransactionCase):

    post_install = True
    at_install = False

    def setUp(self):
        super(TestLetsencrypt, self).setUp()

    def test_config_settings(self):
        settings_model = self.env['base.config.settings']
        letsencrypt_model = self.env['letsencrypt']
        settings = settings_model.create({
            'dns_provider': 'shell',
            'script': 'touch /tmp/.letsencrypt_test',
            'altnames':
                'test.test.com',
            'reload_command': 'echo',
            })
        settings.set_dns_provider()
        setting_vals = settings.default_get([])
        self.assertEquals(setting_vals['dns_provider'], 'shell')
        letsencrypt_model._call_cmdline(setting_vals['script'], shell=True)
        self.assertEquals(path.exists('/tmp/.letsencrypt_test'), True)
        self.assertEquals(
            setting_vals['altnames'],
            settings.altnames,
        )
        self.assertEquals(setting_vals['reload_command'], 'echo')
        settings.onchange_altnames()
        self.assertEquals(settings.needs_dns_provider, False)

    @mock.patch('odoo.addons.letsencrypt.models.letsencrypt')
    def test_letsencrypt(self, mock_obj):
        letsencrypt = self.env['letsencrypt']
        mockV2 = mock.Mock
        order_resource = mock.Mock
        order_resource.fullchain_pem = 'test'
        mockV2.poll_and_finalize = order_resource
        authorization = mock.Mock
        body = mock.Mock
        challenge_http = mock.Mock
        challenge_http.chall = mock.Mock
        challenge_http.chall.typ = 'http-01'
        challenge_http.chall.token = 'a_token'
        challenge_dns = mock.Mock
        challenge_dns.chall = mock.Mock
        challenge_dns.chall.typ = 'dns-01'
        challenge_dns.chall.token = 'a_token'
        challenges = [challenge_dns, challenge_http]
        body.challenges = challenges
        authorization.body = body
        mockV2.new_order = mock.Mock(side_effect=lambda x: mock.Mock(
            authorizations=[authorization]))
        mock_obj.client.ClientV2 = mockV2(create=True)
        with self.assertRaises(errors.Error):
            letsencrypt._cron()
            account_key_file = letsencrypt._generate_key('account_key')
            account_key = jose.JWKRSA.load(open(account_key_file).read())
            domain = urlparse.urlparse(
                self.env['ir.config_parameter'].get_param(
                    'web.base.url', 'localhost')).netloc
            domain_key_file = letsencrypt._generate_key(domain)
            mockV2.new_order.assert_called_with(letsencrypt._make_csr(
                account_key,
                domain_key_file,
                domain))
            mockV2._respond_challenge_dns.assert_called_with(
                challenge_dns,
                domain)
