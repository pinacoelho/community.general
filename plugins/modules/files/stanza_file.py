#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2012, Jan-Piet Mens <jpmens () gmail.com>
# Copyright: (c) 2015, Ales Nosek <anosek.nosek () gmail.com>
# Copyright: (c) 2017, Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


DOCUMENTATION = r'''
---
module: stanza_file
short_description: Tweak settings in stanza files
extends_documentation_fragment: files
description:
     - Manage (add, remove, change) individual settings in a stanza-style file without having
       to manage the file as a whole with, say, M(ansible.builtin.template) or M(ansible.builtin.assemble).
     - Adds missing stanzas if they don't exist.
     - Before Ansible 2.0, comments are discarded when the source file is read, and therefore will not show up in the destination file.
     - Since Ansible 2.3, this module adds missing ending newlines to files to keep in line with the POSIX standard, even when
       no other modifications need to be applied.
options:
  path:
    description:
      - Path to the stanza-style file; this file is created if required.
      - Before Ansible 2.3 this option was only usable as I(dest).
    type: path
    required: true
    aliases: [ dest ]
  stanza:
    description:
      - Stanza name in stanza file. This is added if C(state=present) automatically when
        a single value is being set.
      - If left empty or set to C(null), the I(attr) will be placed before the first I(stanza).
      - Using C(null) is also required if the config format does not support stanzas.
    type: str
    required: true
  attr:
    description:
      - If set (required for changing a I(value)), this is the name of the attr.
      - May be omitted if adding/removing a whole I(stanza).
    type: str
  value:
    description:
      - The string value to be associated with an I(attr).
      - May be omitted when removing an I(attr).
      - Mutually exclusive with I(values).
      - I(value=v) is equivalent to I(values=[v]).
    type: str
  values:
    description:
      - The string value to be associated with an I(attr).
      - May be omitted when removing an I(attr).
      - Mutually exclusive with I(value).
      - I(value=v) is equivalent to I(values=[v]).
    type: list
    elements: str
    version_added: 3.6.0
  backup:
    description:
      - Create a backup file including the timestamp information so you can get
        the original file back if you somehow clobbered it incorrectly.
    type: bool
    default: no
  state:
    description:
      - If set to C(absent) and I(exclusive) set to C(yes) all matching I(attr) lines are removed.
      - If set to C(absent) and I(exclusive) set to C(no) the specified C(attr=value) lines are removed,
        but the other I(attr)s with the same name are not touched.
      - If set to C(present) and I(exclusive) set to C(no) the specified C(attr=values) lines are added,
        but the other I(attr)s with the same name are not touched.
      - If set to C(present) and I(exclusive) set to C(yes) all given C(attr=values) lines will be
        added and the other I(attr)s with the same name are removed.
    type: str
    choices: [ absent, present ]
    default: present
  exclusive:
    description:
      - If set to C(yes) (default), all matching I(attr) lines are removed when I(state=absent),
        or replaced when I(state=present).
      - If set to C(no), only the specified I(value(s)) are added when I(state=present),
        or removed when I(state=absent), and existing ones are not modified.
    type: bool
    default: yes
    version_added: 3.6.0
  no_extra_spaces:
    description:
      - Do not insert spaces before and after '=' symbol.
    type: bool
    default: no
  create:
    description:
      - If set to C(no), the module will fail if the file does not already exist.
      - By default it will create the file if it is missing.
    type: bool
    default: yes
  allow_no_value:
    description:
      - Allow attr without value and without '=' symbol.
    type: bool
    default: no
notes:
   - While it is possible to add an I(attr) without specifying a I(value), this makes no sense.
   - As of Ansible 2.3, the I(dest) attr has been changed to I(path) as default, but I(dest) still works as well.
   - As of community.general 3.2.0, UTF-8 BOM markers are discarded when reading files.
author:
    - Jan-Piet Mens (@jpmens)
    - Ales Nosek (@noseka1)
'''

EXAMPLES = r'''
# Before Ansible 2.3, option 'dest' was used instead of 'path'
- name: Ensure "fav=lemonade is in stanza "drinks:" in specified file
  community.general.stanza_file:
    path: /etc/conf
    stanza: drinks
    attr: fav
    value: lemonade
    mode: '0600'
    backup: yes

- name: Ensure "temperature=cold is in stanza "drinks:" in specified file
  community.general.stanza_file:
    path: /etc/anotherconf
    stanza: drinks
    attr: temperature
    value: cold
    backup: yes

- name: Add "beverage=lemon juice" is in stanza "drinks:" in specified file
  community.general.stanza_file:
    path: /etc/conf
    stanza: drinks
    attr: beverage
    value: lemon juice
    mode: '0600'
    state: present
    exclusive: no

- name: Ensure multiple values "beverage=coke" and "beverage=pepsi" are in stanza "drinks:" in specified file
  community.general.stanza_file:
    path: /etc/conf
    stanza: drinks
    attr: beverage
    values:
      - coke
      - pepsi
    mode: '0600'
    state: present
'''

import io
import os
import re
import tempfile
import traceback

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.common.text.converters import to_bytes, to_text


def match_attr(attr, line):
    attr = re.escape(attr)
    return re.match('[#;]?( |\t)*(%s)( |\t)*(=|$)( |\t)*(.*)' % attr, line)


def match_active_attr(attr, line):
    attr = re.escape(attr)
    return re.match('( |\t)*(%s)( |\t)*(=|$)( |\t)*(.*)' % attr, line)


def update_stanza_line(changed, stanza_lines, index, changed_lines, newline, msg):
    attr_changed = stanza_lines[index] != newline
    changed = changed or attr_changed
    if attr_changed:
        msg = 'attr changed'
    stanza_lines[index] = newline
    changed_lines[index] = 1
    return (changed, msg)


def do_stanza(module, filename, stanza=None, attr=None, values=None,
           state='present', exclusive=True, backup=False, no_extra_spaces=False,
           create=True, allow_no_value=False):

    if stanza is not None:
        stanza = to_text(stanza)
    if attr is not None:
        attr = to_text(attr)

    # deduplicate entries in values
    values_unique = []
    [values_unique.append(to_text(value)) for value in values if value not in values_unique and value is not None]
    values = values_unique

    diff = dict(
        before='',
        after='',
        before_header='%s (content)' % filename,
        after_header='%s (content)' % filename,
    )

    if not os.path.exists(filename):
        if not create:
            module.fail_json(rc=257, msg='Destination %s does not exist!' % filename)
        destpath = os.path.dirname(filename)
        if not os.path.exists(destpath) and not module.check_mode:
            os.makedirs(destpath)
        stanza_lines = []
    else:
        with io.open(filename, 'r', encoding="utf-8-sig") as stanza_file:
            stanza_lines = [to_text(line) for line in stanza_file.readlines()]

    if module._diff:
        diff['before'] = u''.join(stanza_lines)

    changed = False

    # stanza file could be empty
    if not stanza_lines:
        stanza_lines.append(u'\n')

    # last line of file may not contain a trailing newline
    if stanza_lines[-1] == u"" or stanza_lines[-1][-1] != u'\n':
        stanza_lines[-1] += u'\n'
        changed = True

    # append fake stanza lines to simplify the logic
    # At top:
    # Fake random stanza to do not match any other in the file
    # Using commit hash as fake stanza name
    fake_stanza_name = u"ad01e11446efb704fcdbdb21f2c43757423d91c5"

    # Insert it at the beginning
    stanza_lines.insert(0, u'[%s]' % fake_section_name)

    # At bottom:
    stanza_lines.append(u'[')

    # If no stanza is defined, fake stanza is used
    if not stanza:
        stanza = fake_stanza_name

    within_stanza = not stanza
    stanza_start = stanza_end = 0
    msg = 'OK'
    if no_extra_spaces:
        assignment_format = u'%s=%s\n'
    else:
        assignment_format = u'%s = %s\n'

    attr_no_value_present = False

    non_blank_non_comment_pattern = re.compile(to_text(r'^[ \t]*([#;].*)?$'))

    before = after = []
    stanza_lines = []

    for index, line in enumerate(stanza_lines):
        # find start and end of stanza
        if line.startswith(u'[%s]' % stanza):
            within_stanza = True
            stanza_start = index
        elif line.startswith(u'['):
            if within_stanza:
                stanza_end = index
                break

    before = stanza_lines[0:stanza_start]
    stanza_lines = stanza_lines[stanza_start:stanza_end]
    after = stanza_lines[stanza_end:len(stanza_lines)]

    # Keep track of changed stanza_lines
    changed_lines = [0] * len(stanza_lines)

    # handling multiple instances of attr=value when state is 'present' with/without exclusive is a bit complex
    #
    # 1. edit all lines where we have a attr=value pair with a matching value in values[]
    # 2. edit all the remaing lines where we have a matching attr
    # 3. delete remaining lines where we have a matching attr
    # 4. insert missing attr line(s) at the end of the stanza

    if state == 'present' and attr:
        for index, line in enumerate(stanza_lines):
            if match_attr(attr, line):
                match = match_attr(attr, line)
                if values and match.group(6) in values:
                    matched_value = match.group(6)
                    if not matched_value and allow_no_value:
                        # replace existing attr with no value line(s)
                        newline = u'%s\n' % attr
                        attr_no_value_present = True
                    else:
                        # replace existing attr=value line(s)
                        newline = assignment_format % (attr, matched_value)
                    (changed, msg) = update_stanza_line(changed, stanza_lines, index, changed_lines, newline, msg)
                    values.remove(matched_value)
                elif not values and allow_no_value:
                    # replace existing attr with no value line(s)
                    newline = u'%s\n' % attr
                    (changed, msg) = update_stanza_line(changed, stanza_lines, index, changed_lines, newline, msg)
                    attr_no_value_present = True
                    break

    if state == 'present' and exclusive and not allow_no_value:
        # override attr with no value to attr with value if not allow_no_value
        if len(values) > 0:
            for index, line in enumerate(stanza_lines):
                if not changed_lines[index] and match_active_attr(attr, stanza_lines[index]):
                    newline = assignment_format % (attr, values.pop(0))
                    (changed, msg) = update_stanza_line(changed, stanza_lines, index, changed_lines, newline, msg)
                    if len(values) == 0:
                        break
        # remove all remaining attr occurrences from the rest of the stanza
        for index in range(len(stanza_lines) - 1, 0, -1):
            if not changed_lines[index] and match_active_attr(attr, stanza_lines[index]):
                del stanza_lines[index]
                del changed_lines[index]
                changed = True
                msg = 'attr changed'

    if state == 'present':
        # insert missing attr line(s) at the end of the stanza
        for index in range(len(stanza_lines), 0, -1):
            # search backwards for previous non-blank or non-comment line
            if not non_blank_non_comment_pattern.match(stanza_lines[index - 1]):
                if attr and values:
                    # insert attr line(s)
                    for element in values[::-1]:
                        # items are added backwards, so traverse the list backwards to not confuse the user
                        # otherwise some of their attrs might appear in reverse order for whatever fancy reason ¯\_(ツ)_/¯
                        if element is not None:
                            # insert attr=value line
                            stanza_lines.insert(index, assignment_format % (attr, element))
                            msg = 'attr added'
                            changed = True
                        elif element is None and allow_no_value:
                            # insert attr with no value line
                            stanza_lines.insert(index, u'%s\n' % attr)
                            msg = 'attr added'
                            changed = True
                elif attr and not values and allow_no_value and not attr_no_value_present:
                    # insert attr with no value line(s)
                    stanza_lines.insert(index, u'%s\n' % attr)
                    msg = 'attr added'
                    changed = True
                break

    if state == 'absent':
        if attr:
            if exclusive:
                # delete all attr line(s) with given attr and ignore value
                new_stanza_lines = [line for line in stanza_lines if not (match_active_attr(attr, line))]
                if stanza_lines != new_stanza_lines:
                    changed = True
                    msg = 'attr changed'
                    stanza_lines = new_stanza_lines
            elif not exclusive and len(values) > 0:
                # delete specified attr=value line(s)
                new_stanza_lines = [i for i in stanza_lines if not (match_active_attr(attr, i) and match_active_attr(attr, i).group(6) in values)]
                if stanza_lines != new_stanza_lines:
                    changed = True
                    msg = 'attr changed'
                    stanza_lines = new_stanza_lines
        else:
            # drop the entire stanza
            if stanza_lines:
                stanza_lines = []
                msg = 'stanza removed'
                changed = True

    # reassemble the stanza_lines after manipulation
    stanza_lines = before + stanza_lines + after

    # remove the fake stanza line
    del stanza_lines[0]
    del stanza_lines[-1:]

    if not within_stanza and state == 'present':
        stanza_lines.append(u'[%s]\n' % stanza)
        msg = 'stanza and attr added'
        if attr and values:
            for value in values:
                stanza_lines.append(assignment_format % (attr, value))
        elif attr and not values and allow_no_value:
            stanza_lines.append(u'%s\n' % attr)
        else:
            msg = 'only stanza added'
        changed = True

    if module._diff:
        diff['after'] = u''.join(stanza_lines)

    backup_file = None
    if changed and not module.check_mode:
        if backup:
            backup_file = module.backup_local(filename)

        encoded_stanza_lines = [to_bytes(line) for line in stanza_lines]
        try:
            tmpfd, tmpfile = tempfile.mkstemp(dir=module.tmpdir)
            f = os.fdopen(tmpfd, 'wb')
            f.writelines(encoded_stanza_lines)
            f.close()
        except IOError:
            module.fail_json(msg="Unable to create temporary file %s", traceback=traceback.format_exc())

        try:
            module.atomic_move(tmpfile, filename)
        except IOError:
            module.ansible.fail_json(msg='Unable to move temporary \
                                   file %s to %s, IOError' % (tmpfile, filename), traceback=traceback.format_exc())

    return (changed, backup_file, diff, msg)


def main():

    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type='path', required=True, aliases=['dest']),
            stanza=dict(type='str', required=True),
            attr=dict(type='str'),
            value=dict(type='str'),
            values=dict(type='list', elements='str'),
            backup=dict(type='bool', default=False),
            state=dict(type='str', default='present', choices=['absent', 'present']),
            exclusive=dict(type='bool', default=True),
            no_extra_spaces=dict(type='bool', default=False),
            allow_no_value=dict(type='bool', default=False),
            create=dict(type='bool', default=True)
        ),
        mutually_exclusive=[
            ['value', 'values']
        ],
        add_file_common_args=True,
        supports_check_mode=True,
    )

    path = module.params['path']
    stanza = module.params['stanza']
    attr = module.params['attr']
    value = module.params['value']
    values = module.params['values']
    state = module.params['state']
    exclusive = module.params['exclusive']
    backup = module.params['backup']
    no_extra_spaces = module.params['no_extra_spaces']
    allow_no_value = module.params['allow_no_value']
    create = module.params['create']

    if state == 'present' and not allow_no_value and value is None and not values:
        module.fail_json(msg="Parameter 'value(s)' must be defined if state=present and allow_no_value=False.")

    if value is not None:
        values = [value]
    elif values is None:
        values = []

    (changed, backup_file, diff, msg) = do_stanza(module, path, stanza, attr, values, state, exclusive, backup, no_extra_spaces, create, allow_no_value)

    if not module.check_mode and os.path.exists(path):
        file_args = module.load_file_common_arguments(module.params)
        changed = module.set_fs_attributes_if_different(file_args, changed)

    results = dict(
        changed=changed,
        diff=diff,
        msg=msg,
        path=path,
    )
    if backup_file is not None:
        results['backup_file'] = backup_file

    # Mission complete
    module.exit_json(**results)


if __name__ == '__main__':
    main()
