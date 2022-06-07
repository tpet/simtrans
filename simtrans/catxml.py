# -*- coding:utf-8 -*-

"""Utility command to concatinate two or more xml files to one
"""

import sys
from optparse import OptionParser, OptionError
import lxml.etree
from . import utils


def main():
    usage = '''Usage: %prog [options] xmlfile1 xmlfile2 ...
Concatinate multiple xml files.'''
    parser = OptionParser(usage=usage)
    parser.add_option('-v', '--verbose', action='store_true', dest='verbose', default=False, help='verbose output')
    try:
        options, args = parser.parse_args()
    except OptionError as e:
        print('OptionError: ', e, file=sys.stderr)
        print(parser.print_help(), file=sys.stderr)
        return 1

    if len(args) <= 1:
        print(parser.print_help(), file=sys.stderr)
        return 1

    d = lxml.etree.parse(utils.resolveFile(args[0]))
    r = d.getroot()
    for f in args[1:]:
        d2 = lxml.etree.parse(utils.resolveFile(f))
        r.extend(d2.getroot())

    print(lxml.etree.tostring(r, pretty_print=True))

    return 0

if __name__ == '__main__':
    sys.exit(main())
