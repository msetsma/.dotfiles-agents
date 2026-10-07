from entra_tool.errors import die
from entra_tool.models import Options
from entra_tool.text import is_guid


SEARCH_TARGETS = {
    'search',
    'search-users',
    'user-search',
    'users-search',
    'search-groups',
    'group-search',
    'groups-search',
    'dept',
}
USER_SEARCH_TARGETS = {'search', 'search-users', 'user-search', 'users-search'}
GROUP_SEARCH_TARGETS = {'search-groups', 'group-search', 'groups-search'}
CACHE_TARGETS = {'cache-status', 'cache-refresh'}
COMPARE_TARGETS = {'compare', 'compare-users', 'compare-groups'}
COMPARE_USER_ALIASES = ('compare-users', 'compare-user', 'users-compare', 'user-compare')
COMPARE_GROUP_ALIASES = ('compare-groups', 'compare-group', 'groups-compare', 'group-compare')
SEARCH_COMMANDS = (
    'search',
    'users',
    'groups',
    'dept',
    'department',
    'departments',
    'search-users',
    'user-search',
    'users-search',
    'search-groups',
    'group-search',
    'groups-search',
)
FIRST_ARGUMENT_ERROR = (
    "First argument must be 'user', 'group', 'compare', 'compare-users', "
    "'compare-groups', 'reports', 'users', 'groups', 'dept', 'search', "
    "'search-groups', or 'cache'"
)


def read_compare_identifiers(args: list[str]) -> tuple[str, str]:
    left_identifier = ''
    right_identifier = ''
    if args and not args[0].startswith('--'):
        left_identifier = args.pop(0)
    if args and not args[0].startswith('--'):
        right_identifier = args.pop(0)
    if args and not args[0].startswith('--'):
        die('compare accepts at most two identifiers. Quote group names that contain spaces.')
    return left_identifier, right_identifier


def read_initial_query(args: list[str]) -> str:
    if args and not args[0].startswith('--'):
        return args.pop(0)
    return ''


def parse_compare_target(target_type: str, args: list[str]) -> tuple[str, str, str]:
    """Return (target_type, left_identifier, right_identifier) for a compare command."""
    left_identifier = ''
    right_identifier = ''
    if target_type == 'compare':
        if args and not args[0].startswith('--'):
            kind = args.pop(0)
            if kind in ('user', 'users'):
                target_type = 'compare-users'
                left_identifier, right_identifier = read_compare_identifiers(args)
            elif kind in ('group', 'groups'):
                target_type = 'compare-groups'
                left_identifier, right_identifier = read_compare_identifiers(args)
            else:
                die('compare requires users or groups. Try: entra compare users')
    elif target_type in COMPARE_USER_ALIASES:
        target_type = 'compare-users'
        left_identifier, right_identifier = read_compare_identifiers(args)
    else:
        target_type = 'compare-groups'
        left_identifier, right_identifier = read_compare_identifiers(args)
    return target_type, left_identifier, right_identifier


def parse_search_target(target_type: str, args: list[str]) -> tuple[str, str]:
    """Return (target_type, initial_query) for a search, users, groups, or dept command."""
    if target_type == 'search':
        if args and args[0] in ('group', 'groups'):
            target_type = 'search-groups'
            args.pop(0)
        elif args and args[0] in ('user', 'users'):
            target_type = 'search-users'
            args.pop(0)
    elif target_type == 'users':
        target_type = 'search-users'
    elif target_type == 'groups':
        target_type = 'search-groups'
    elif target_type in ('dept', 'department', 'departments'):
        target_type = 'dept'
    return target_type, read_initial_query(args)


def parse_reports_target(args: list[str]) -> tuple[str, str]:
    """Return (identifier, initial_query) for a reports command."""
    if args and not args[0].startswith('--'):
        value = args.pop(0)
        if is_guid(value) or '@' in value:
            return value, ''
        return '', value
    return '', ''


def parse_cache_target(args: list[str]) -> str:
    if not args or args[0].startswith('--'):
        die('cache requires a subcommand: status or refresh')
    subcommand = args.pop(0)
    if subcommand not in ('status', 'refresh'):
        die('cache requires a subcommand: status or refresh')
    return f'cache-{subcommand}'


def parse_target(args: list[str]) -> Options:
    target_type = args.pop(0)
    identifier = ''
    initial_query = ''
    left_identifier = ''
    right_identifier = ''

    if target_type == 'compare' or target_type in COMPARE_USER_ALIASES or target_type in COMPARE_GROUP_ALIASES:
        target_type, left_identifier, right_identifier = parse_compare_target(target_type, args)
    elif target_type in SEARCH_COMMANDS:
        target_type, initial_query = parse_search_target(target_type, args)
    elif target_type in ('user', 'group'):
        if not args:
            die(
                f"Missing {target_type} identifier. Try: entra {target_type} <upn-or-id>. Run 'entra --help' for details."
            )
        identifier = args.pop(0)
    elif target_type in ('reports', 'report', 'org', 'org-reports'):
        target_type = 'reports'
        identifier, initial_query = parse_reports_target(args)
    elif target_type == 'cache':
        target_type = parse_cache_target(args)
    else:
        die(FIRST_ARGUMENT_ERROR)

    return Options(
        target_type=target_type,
        identifier=identifier,
        initial_query=initial_query,
        left_identifier=left_identifier,
        right_identifier=right_identifier,
    )
