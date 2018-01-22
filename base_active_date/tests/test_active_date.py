# -*- coding: utf-8 -*-
# Copyright - 2018 Therp BV <https://therp.nl>.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from datetime import datetime

from odoo.tests import common


class TestActiveDate(common.TransactionCase):

    def test_compute_active(self):
        """Test function for past, present and future active records."""
        test_model = self.env['active.date.test']
        # Create a record without start and end date. It should be active.
        forever_active = test_model.create({
            'code': 'FOREVER',
            'name': 'Record should be active forever'})
        self.assertTrue(forever_active.active)
        date_today = datetime.date.today()
        today = date_today.strftime("%Y-%m-%d")
        one_week = datetime.timedelta(days=7)
        date_last_week = today - one_week
        last_week = date_last_week.strftime("%Y-%m-%d")
        date_next_week = today + one_week
        next_week = date_next_week.strftime("%Y-%m-%d")
        # Create a record started before today, ends after today
        now_active = test_model.create({
            'code': 'NOW',
            'name': 'Record should be active now',
            'active_date_start': last_week,
            'active_date_end': next_week})
        self.assertTrue(now_active.active)
        # Create record that was active until last week
        last_week_active = test_model.create({
            'code': 'LAST WEEK',
            'name': 'Record was active until last week',
            'active_date_end': last_week})
        self.assertFalse(last_week_active.active)
        # Create record that only will become active next week
        next_week_active = test_model.create({
            'code': 'NEXT WEEK',
            'name': 'Record will become active next week',
            'active_date_start': next_week})
        self.assertFalse(next_week_active.active)
