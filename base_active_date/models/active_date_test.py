# -*- coding: utf-8 -*-
# Copyright - 2018 Therp BV <https://therp.nl>.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from odoo import fields, model


class ActiveDateTest(models.Model):
    _name = 'active.date.test'
    _inherit = ['active.date']
    _description = "Just for testing active.date"

    code = fields.Char()
    name = fields.Char()
