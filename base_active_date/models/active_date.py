# -*- coding: utf-8 -*-
# Copyright - 2017 Therp BV.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
import logging
from psycopg2.extensions import AsIs

from odoo import _, api, fields, models


_logger = logging.getLogger(__name__)


SQL_SET_ACTIVE = \
    """UPDATE %s
 SET active = true,
     active_change_datetime = %s
 WHERE (active IS NULL OR NOT active)
   AND (active_date_start IS NULL OR active_date_start <= CURRENT_DATE)
   AND (active_date_end IS NULL OR active_date_end > CURRENT_DATE)"""

SQL_SET_INACTIVE = \
    """UPDATE %s
 SET active = false,
     active_change_datetime = %s
 WHERE (active IS NULL OR active)
   AND ((NOT active_date_start IS NULL AND active_date_start > CURRENT_DATE)
   OR (NOT active_date_end IS NULL AND active_date_end <= CURRENT_DATE))"""


class ActiveDate(models.AbstractModel):
    _name = 'active.date'
    _description = "Mixin for date dependend active"

    @api.depends('active_date_start', 'active_date_end')
    def _compute_active(self):
        """Compute active state.

        Although field active will be computed on create and each time
        start- or enddate changes, this is not enough, as the field is also
        dependent on the current date. Automatic recomputation is part of
        a cron job that should be run very close to but after midnight.

        For cases where there can be uncertainty wether the cron job has run,
        an on the fly recomputation is also provided.
        """
        active_change_datetime = fields.Datetime.now()
        today = fields.Date.today()
        any_change = False
        for this in self:
            save_active = this.active
            new_active = False
            if ((not this.active_date_start or
                    this.active_date_start <= today) and
                    (not this.active_date_end or
                     this.active_date_end >= today)):
                new_active = True
            if new_active != save_active:
                any_change = True
                this.active = new_active
                this.active_change_datetime = active_change_datetime
        if any_change:
            self.active_refresh_post_process(active_change_datetime)

    # All fields provided by mixin start with active, to prevent name clashes
    active_date_start = fields.Date(
        string='Start date',
        index=True,
        help="Date that record becomes active")
    active_date_end = fields.Date(
        string='End date',
        index=True,
        help="Day that record becomes inactive")
    active = fields.Boolean(
        string='Active',
        compute='_compute_active',
        default=True,  # Only to provide initial value
        store=True,
        index=True,
        help="Active depends on start date, end date and current date")
    active_change_datetime = fields.Datetime(
        string='Timestamp active change',
        readonly=True,
        index=True,
        help="Technical field to select all records changed by the last"
             " run of active_refresh()")

    @api.model
    def active_refresh_post_process(self, active_change_datetime):
        """Postprocess records immediately after changing active field."""
        pass

    @api.model
    def active_refresh(self):
        """Refresh the active field for all records where needed."""
        active_change_datetime = fields.Datetime.now()
        cr = self.env.cr
        try:
            cr.execute(
                SQL_SET_ACTIVE, (AsIs(self._table), active_change_datetime))
            cr.commit()
        except Exception as e:
            _logger.error(_(
                "Exception when setting records to active for model %s:\n"
                "%s") % (self._name, e))
        try:
            cr.execute(
                SQL_SET_INACTIVE, (AsIs(self._table), active_change_datetime))
            cr.commit()
        except Exception as e:
            _logger.error(_(
                "Exception when setting records to inactive for model %s:\n"
                "%s") % (self._name, e))
        self.active_refresh_post_process(active_change_datetime)

    @api.model
    def active_date_refresh_all_cron(self):
        """Find all models with an active_refresh method, and call this."""
        for model_name in self.env.registry.models:
            model = self.env[model_name]
            if hasattr(model, 'active_refresh'):
                _logger.info(_(
                    "Updating active field for model %s:") % model_name)
                model.active_refresh()
