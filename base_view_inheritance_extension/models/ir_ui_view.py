# -*- coding: utf-8 -*-
# © 2016 Therp BV <http://therp.nl>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from lxml import etree
from openerp import api, models, tools


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    @api.model
    def apply_inheritance_specs(self, source, specs_tree, inherit_id):
        for specs, handled_by in self._iter_inheritance_specs(specs_tree):
            source = handled_by(source, specs, inherit_id)
        return source

    @api.model
    def _iter_inheritance_specs(self, spec):
        if spec.tag == 'data':
            for child in spec:
                for node, handler in self._iter_inheritance_specs(child):
                    yield node, handler
        if spec.get('position') == 'attributes':
            for child in spec:
                node = etree.Element(spec.tag, **spec.attrib)
                node.insert(0, child)
                yield node, self._get_inheritance_handler_attributes(
                    child
                )
            return
        yield spec, self._get_inheritance_handler(spec)

    @api.model
    def _get_inheritance_handler(self, node):
        handler = super(IrUiView, self).apply_inheritance_specs
        if hasattr(
            self, 'inheritance_handler_%s' % node.tag
        ):
            handler = getattr(
                self,
                'inheritance_handler_%s' % node.tag
            )
        return handler

    @api.model
    def _get_inheritance_handler_attributes(self, node):
        handler = super(IrUiView, self).apply_inheritance_specs
        if hasattr(
            self, 'inheritance_handler_attributes_%s' % node.get('operation')
        ):
            handler = getattr(
                self,
                'inheritance_handler_attributes_%s' % node.get('operation')
            )
        return handler

    @api.model
    def inheritance_handler_attributes_python_dict(
        self, source, specs, inherit_id
    ):
        """Implement
        <$node position="attributes">
            <attribute name="$attribute" operation="python_dict" key="$key">
                $keyvalue
            </attribute>
        </$node>"""
        node = self.locate_node(source, specs)
        for attribute_node in specs:
            python_dict = tools.safe_eval(
                node.get(attribute_node.get('name')) or '{}',
                tools.misc.UnquoteEvalContext()
            )
            python_dict[attribute_node.get('key')] = attribute_node.text
            node.attrib[attribute_node.get('name')] = str(python_dict)
        return source

    @api.model
    def inheritance_handler_xpath(self, source, specs, inherit_id):
        if not specs.get('position') == 'move':
            return super(IrUiView, self).apply_inheritance_specs(
                source, specs, inherit_id
            )
        node = self.locate_node(source, specs)
        target_node = self.locate_node(
            source, etree.Element(specs.tag, expr=specs.get('target'))
        )
        target_node.append(node)
        return source
