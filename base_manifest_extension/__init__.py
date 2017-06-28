# -*- coding: utf-8 -*-
# © 2017 Therp BV <http://therp.nl>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
import inspect
from openerp.sql_db import Cursor
from openerp.modules import module
original = module.load_information_from_description_file


def load_information_from_description_file(module, mod_path=None):
    result = original(module, mod_path=mod_path)
    # add the keys you want to react on here
    if result.get('depends_if_installed'):
        cr = None
        for frame, filename, lineno, funcname, line, index in inspect.stack():
            # walk up the stack until we've found a cursor
            if 'cr' in frame.f_locals and isinstance(
                frame.f_locals['cr'], Cursor
            ):
                cr = frame.f_locals['cr']
                break
        if cr:
            _handle_depends_if_installed(cr, result)

    return result

def _handle_depends_if_installed(cr, manifest):
    if not manifest.get('depends_if_installed'):
        return
    cr.execute(
        'select name from ir_module_module '
        'where state in %s and name in %s',
        (
            tuple(['installed', 'to install', 'to upgrade']),
            tuple(manifest['depends_if_installed']),
        ),
    )
    manifest.pop('depends_if_installed')
    depends = manifest.setdefault('depends', [])
    depends.extend(module for module, in cr.fetchall())


module.load_information_from_description_file =\
    load_information_from_description_file
