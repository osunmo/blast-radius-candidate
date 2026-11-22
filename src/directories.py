"""Query baseline filesystem observations for regular SQL fixture additions."""
from pathlib import PurePosixPath


def addition_consumers(path, data):
    """Return a bit mask, or None when this certificate cannot prove the change.

    Listing a pre-existing ancestor above the actual parent is unchanged. New
    directories do change their nearest existing ancestor's entry names.
    """
    if not data.get('complete') or data.get('version') != 1:
        return None
    candidate = PurePosixPath(path)
    if (str(candidate) != path or candidate.is_absolute() or '..' in candidate.parts or ':' in path
            or candidate.suffix.lower() != '.sql'
            or not candidate.is_relative_to(PurePosixPath('tests/fixtures'))):
        return None
    if path in data.get('known_files', ()):
        return None
    symlinks = set(data.get('symlinks', ()))
    if any(str(parent) in symlinks for parent in (candidate, *candidate.parents)):
        return None
    known = set(data['known_directories'])
    selected = int(data.get('always_include', '0'), 16)
    directories = data.get('directories', {})
    probes = data.get('probes', {})
    opens = data.get('opens', {})
    selected |= int(probes.get(path, '0'), 16) | int(opens.get(path, '0'), 16)
    parent = candidate.parent
    while True:
        key = str(parent)
        # Directory metadata (mtime, size, link count) can change even when the
        # directory already exists; observations cannot assume existence-only.
        selected |= (int(directories.get(key, '0'), 16)
                     | int(probes.get(key, '0'), 16) | int(opens.get(key, '0'), 16))
        if key in known:
            break
        if parent == parent.parent or key == '.':
            return None
        parent = parent.parent
    return selected
