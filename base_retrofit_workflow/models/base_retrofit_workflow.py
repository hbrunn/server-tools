# -*- coding: utf-8 -*-
# Copyright 2019 Therp BV <https://therp.nl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
from openerp import api, models


class BaseRetrofitWorkflow(models.AbstractModel):
    _name = 'base.retrofit.workflow'

    @api.model
    def _has_workflow(self, records):
        """determine if some records already have a workflow instance"""
        self.env.cr.execute(
            'select count(id) from wkf_instance '
            "where res_type=%s and res_id in %s and state='active'",
            (records._name, tuple(records.ids or [False])),
        )
        return self.env.cr.fetchone()[0] >= len(records)

    @api.model
    def _create_workflow(
            self, records, workflow=False, create_start_activities=True,
            start_activities_state='complete',
    ):
        """create a workflow instance and initialize start acttivities,
        return ids of instances created"""
        if not workflow:
            workflow = self.env['workflow'].search(
                [('osv', '=', records._name)], order='on_create desc', limit=1,
            )
        assert workflow, 'Your model does not seem to have a workflow'
        self.env.cr.execute(
            """insert into wkf_instance (uid, wkf_id, res_type, res_id, state)
            select %s, %s, %s, id, 'active'
            from unnest(%s) as t(id)
            returning id""",
            (self.env.user.id, workflow.id, records._name, records.ids),
        )
        instance_ids = [_id for _id, in self.env.cr.fetchall()]
        if not create_start_activities:
            return instance_ids

        activities = self.env['workflow.activity'].search([
            ('wkf_id', '=', workflow.id),
            ('flow_start', '=', True),
        ])
        self.env.cr.execute(
            """insert into wkf_workitem (act_id, inst_id, state)
            select activity_id, instance_id, %s
            from
            unnest(%s) as t1(activity_id) join
            unnest(%s) as t2(instance_id) on true""",
            (start_activities_state, activities.ids, instance_ids)
        )
        return instance_ids

    @api.model
    def _set_workflow_activity(
            self, records, activity, activitiy_state='complete', workflow=None,
    ):
        """set records' workflow state to activity"""
        if not workflow:
            workflow = self.env['workflow'].search(
                [('osv', '=', records._name)], order='on_create desc', limit=1,
            )
        assert workflow, 'Your model does not seem to have a workflow'
        self.env.cr.execute(
            """delete from wkf_workitem
            using wkf_instance
            where wkf_instance.id=wkf_workitem.inst_id and
            wkf_instance.res_id in %s and
            wkf_instance.wkf_id in %s""",
            (tuple(records.ids or [False]), tuple(workflow.ids or [False])),
        )
        self.env.cr.execute(
            """insert into wkf_workitem (act_id, inst_id, state)
            select %s, id, %s
            from wkf_instance
            where
            wkf_instance.res_id in %s and wkf_instance.wkf_id in %s""",
            (
                activity.id, activitiy_state, tuple(records.ids or [False]),
                tuple(workflow.ids or [False]),
            ),
        )
